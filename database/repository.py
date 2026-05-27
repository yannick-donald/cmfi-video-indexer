from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from database.models import VideoRecord
from database.schema import init_database

SORT_COLUMNS = {
    "file_name": "file_name COLLATE NOCASE",
    "folder_path": "folder_path COLLATE NOCASE",
    "file_size": "file_size",
    "duration_seconds": "duration_seconds",
    "modified_at": "modified_at",
    "resolution": "resolution",
    "file_extension": "file_extension",
    "internal_video_id": "internal_video_id",
    "speaker": "speaker COLLATE NOCASE",
    "main_theme": "main_theme COLLATE NOCASE",
}


@dataclass(slots=True)
class SearchFilters:
    query: str = ""
    folder: str = ""
    extension: str = ""
    resolution: str = ""
    year: str = ""
    min_size_mb: float | None = None
    max_size_mb: float | None = None
    min_duration_sec: float | None = None
    max_duration_sec: float | None = None
    has_audio: bool | None = None
    shared_drive: str = ""


@dataclass(slots=True)
class SearchResult:
    items: list[VideoRecord]
    total: int
    page: int
    page_size: int
    total_pages: int


class VideoRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        init_database(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_modified_map(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT file_id, modified_at FROM videos").fetchall()
        return {row["file_id"]: row["modified_at"] or "" for row in rows}

    def upsert_video(self, video: VideoRecord) -> None:
        if not video.internal_video_id:
            video.internal_video_id = self.ensure_internal_video_id(video.file_id)

        payload = video.to_dict()
        if payload.get("has_audio") is not None:
            payload["has_audio"] = int(bool(payload["has_audio"]))
        columns = list(payload.keys())
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{col}=excluded.{col}" for col in columns if col != "file_id")
        sql = f"""
            INSERT INTO videos ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(file_id) DO UPDATE SET {updates}
        """
        with self._connect() as conn:
            conn.execute(sql, [payload[col] for col in columns])
            self._upsert_fts(conn, payload)
            conn.commit()

    def ensure_internal_video_id(self, file_id: str) -> str:
        """
        Stable internal ID mapping, preserved across rescans and moves.
        Format: CHR-VID-000001
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT internal_video_id FROM video_internal_ids WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            if row and row[0]:
                return str(row[0])

            # Insert to get a stable autoincrement ID, then format.
            conn.execute(
                "INSERT OR IGNORE INTO video_internal_ids(file_id, created_at) VALUES(?, datetime('now'))",
                (file_id,),
            )
            internal_row = conn.execute(
                "SELECT id, internal_video_id FROM video_internal_ids WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            assert internal_row is not None
            if internal_row["internal_video_id"]:
                return str(internal_row["internal_video_id"])

            numeric_id = int(internal_row["id"])
            internal_id = f"CHR-VID-{numeric_id:06d}"
            conn.execute(
                "UPDATE video_internal_ids SET internal_video_id = ? WHERE file_id = ?",
                (internal_id, file_id),
            )
            conn.commit()
            return internal_id

    def _upsert_fts(self, conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
        conn.execute("DELETE FROM videos_fts WHERE file_id = ?", (payload.get("file_id", ""),))
        conn.execute(
            """
            INSERT INTO videos_fts(
                file_id, internal_video_id, file_name, clean_title, folder_path,
                speaker, ministry, main_theme, biblical_topics, bible_references,
                teaching_type, ai_summary, keywords, semantic_tags
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                payload.get("file_id", ""),
                payload.get("internal_video_id", ""),
                payload.get("file_name", ""),
                payload.get("clean_title", ""),
                payload.get("folder_path", ""),
                payload.get("speaker", ""),
                payload.get("ministry", ""),
                payload.get("main_theme", ""),
                payload.get("biblical_topics", ""),
                payload.get("bible_references", ""),
                payload.get("teaching_type", ""),
                payload.get("ai_summary", ""),
                payload.get("keywords", ""),
                payload.get("semantic_tags", ""),
            ),
        )

    def search(
        self,
        filters: SearchFilters,
        sort_by: str = "file_name",
        sort_dir: str = "asc",
        page: int = 1,
        page_size: int = 50,
        use_fts: bool = False,
    ) -> SearchResult:
        if use_fts and filters.query.strip():
            return self._search_fts(filters, sort_by, sort_dir, page, page_size)

        where, params = self._build_where(filters)
        sort_col = SORT_COLUMNS.get(sort_by, SORT_COLUMNS["file_name"])
        direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
        offset = max(page - 1, 0) * page_size

        count_sql = f"SELECT COUNT(*) FROM videos {where}"
        data_sql = f"""
            SELECT * FROM videos
            {where}
            ORDER BY {sort_col} {direction}, file_name ASC
            LIMIT ? OFFSET ?
        """

        with self._connect() as conn:
            total = conn.execute(count_sql, params).fetchone()[0]
            rows = conn.execute(data_sql, [*params, page_size, offset]).fetchall()

        total_pages = max((total + page_size - 1) // page_size, 1)
        return SearchResult(
            items=[VideoRecord.from_row(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    def _search_fts(
        self,
        filters: SearchFilters,
        sort_by: str,
        sort_dir: str,
        page: int,
        page_size: int,
    ) -> SearchResult:
        query = filters.query.strip()
        offset = max(page - 1, 0) * page_size
        sort_col = SORT_COLUMNS.get(sort_by, SORT_COLUMNS["file_name"])
        direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

        # Use FTS for the query, and keep the rest of filters as normal WHERE clauses.
        where, params = self._build_where(SearchFilters(**{**filters.__dict__, "query": ""}))
        where_prefix = f"{where} AND" if where else "WHERE"

        count_sql = f"""
            SELECT COUNT(*) FROM videos
            WHERE file_id IN (SELECT file_id FROM videos_fts WHERE videos_fts MATCH ?)
        """

        data_sql = f"""
            SELECT * FROM videos
            {where_prefix} file_id IN (SELECT file_id FROM videos_fts WHERE videos_fts MATCH ?)
            ORDER BY {sort_col} {direction}, file_name ASC
            LIMIT ? OFFSET ?
        """

        with self._connect() as conn:
            total = conn.execute(count_sql, (query,)).fetchone()[0]
            rows = conn.execute(data_sql, [*params, query, page_size, offset]).fetchall()

        total_pages = max((total + page_size - 1) // page_size, 1)
        return SearchResult(
            items=[VideoRecord.from_row(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    def get_video(self, file_id: str) -> VideoRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE file_id = ?", (file_id,)).fetchone()
        return VideoRecord.from_row(row) if row else None

    def get_filter_options(self) -> dict[str, list[str]]:
        with self._connect() as conn:
            folders = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT folder_path FROM videos
                    WHERE folder_path IS NOT NULL AND folder_path != ''
                    ORDER BY folder_path COLLATE NOCASE
                    """
                ).fetchall()
            ]
            extensions = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT file_extension FROM videos
                    WHERE file_extension IS NOT NULL AND file_extension != ''
                    ORDER BY file_extension COLLATE NOCASE
                    """
                ).fetchall()
            ]
            resolutions = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT resolution FROM videos
                    WHERE resolution IS NOT NULL AND resolution != ''
                    ORDER BY resolution
                    """
                ).fetchall()
            ]
            years = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT substr(modified_at, 1, 4) AS year
                    FROM videos
                    WHERE modified_at IS NOT NULL AND length(modified_at) >= 4
                    ORDER BY year DESC
                    """
                ).fetchall()
            ]
            shared_drives = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT shared_drive_name FROM videos
                    WHERE shared_drive_name IS NOT NULL AND shared_drive_name != ''
                    ORDER BY shared_drive_name COLLATE NOCASE
                    """
                ).fetchall()
            ]
        return {
            "folders": folders,
            "extensions": extensions,
            "resolutions": resolutions,
            "years": years,
            "shared_drives": shared_drives,
        }

    def get_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            summary = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_videos,
                    COALESCE(SUM(file_size), 0) AS total_bytes,
                    COALESCE(SUM(duration_seconds), 0) AS total_duration_seconds
                FROM videos
                """
            ).fetchone()
            formats = conn.execute(
                """
                SELECT file_extension AS label, COUNT(*) AS count
                FROM videos
                WHERE file_extension IS NOT NULL AND file_extension != ''
                GROUP BY file_extension
                ORDER BY count DESC
                LIMIT 10
                """
            ).fetchall()
            resolutions = conn.execute(
                """
                SELECT resolution AS label, COUNT(*) AS count
                FROM videos
                WHERE resolution IS NOT NULL AND resolution != ''
                GROUP BY resolution
                ORDER BY count DESC
                LIMIT 10
                """
            ).fetchall()
            largest = conn.execute(
                """
                SELECT file_name, file_size, folder_path
                FROM videos
                ORDER BY file_size DESC
                LIMIT 5
                """
            ).fetchall()
            longest = conn.execute(
                """
                SELECT file_name, duration_seconds, folder_path
                FROM videos
                WHERE duration_seconds IS NOT NULL
                ORDER BY duration_seconds DESC
                LIMIT 5
                """
            ).fetchall()

        return {
            "total_videos": summary["total_videos"],
            "total_bytes": summary["total_bytes"],
            "total_duration_seconds": summary["total_duration_seconds"],
            "top_formats": [{"label": row["label"], "count": row["count"]} for row in formats],
            "top_resolutions": [{"label": row["label"], "count": row["count"]} for row in resolutions],
            "largest_files": [
                {
                    "file_name": row["file_name"],
                    "file_size": row["file_size"],
                    "folder_path": row["folder_path"],
                }
                for row in largest
            ],
            "longest_videos": [
                {
                    "file_name": row["file_name"],
                    "duration_seconds": row["duration_seconds"],
                    "folder_path": row["folder_path"],
                }
                for row in longest
            ],
        }

    def _build_where(self, filters: SearchFilters) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if filters.query.strip():
            q = f"%{filters.query.strip()}%"
            clauses.append(
                "(file_name LIKE ? OR folder_path LIKE ? OR parent_folder LIKE ? OR owner LIKE ?)"
            )
            params.extend([q, q, q, q])

        if filters.folder:
            clauses.append("folder_path = ?")
            params.append(filters.folder)

        if filters.extension:
            clauses.append("file_extension = ?")
            params.append(filters.extension)

        if filters.resolution:
            clauses.append("resolution = ?")
            params.append(filters.resolution)

        if filters.year:
            clauses.append("substr(modified_at, 1, 4) = ?")
            params.append(filters.year)

        if filters.shared_drive:
            clauses.append("shared_drive_name = ?")
            params.append(filters.shared_drive)

        if filters.min_size_mb is not None:
            clauses.append("file_size >= ?")
            params.append(int(filters.min_size_mb * 1024 * 1024))

        if filters.max_size_mb is not None:
            clauses.append("file_size <= ?")
            params.append(int(filters.max_size_mb * 1024 * 1024))

        if filters.min_duration_sec is not None:
            clauses.append("duration_seconds >= ?")
            params.append(filters.min_duration_sec)

        if filters.max_duration_sec is not None:
            clauses.append("duration_seconds <= ?")
            params.append(filters.max_duration_sec)

        if filters.has_audio is True:
            clauses.append("has_audio = 1")
        elif filters.has_audio is False:
            clauses.append("(has_audio = 0 OR has_audio IS NULL)")

        if not clauses:
            return "", params
        return "WHERE " + " AND ".join(clauses), params
