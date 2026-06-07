from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
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
    "editorial_title": "editorial_title COLLATE NOCASE",
    "speaker": "speaker COLLATE NOCASE",
    "preacher": "preacher COLLATE NOCASE",
    "main_theme": "main_theme COLLATE NOCASE",
    "content_type": "content_type COLLATE NOCASE",
    "event_name": "event_name COLLATE NOCASE",
    "asset_type": "asset_type COLLATE NOCASE",
    "workflow_stage": "workflow_stage COLLATE NOCASE",
}

ASSET_TYPES = {"raw", "cut"}
WORKFLOW_STAGES = {
    "digitized",
    "to_review",
    "transcribed",
    "treated",
    "ready_edit",
    "published",
}

CHRISTIAN_METADATA_FIELDS = {
    "editorial_title",
    "original_title",
    "alternate_titles",
    "clean_title",
    "normalized_name",
    "speaker",
    "preacher",
    "ministry",
    "main_theme",
    "spiritual_themes",
    "doctrine_topics",
    "biblical_topics",
    "bible_references",
    "songs",
    "worship_leaders",
    "content_type",
    "event_name",
    "event_date",
    "location",
    "language",
    "audience",
    "series_name",
    "session_number",
    "teaching_type",
    "ai_summary",
    "transcript_status",
    "transcript_text_path",
    "transcript_summary",
    "manual_notes",
    "metadata_source",
    "metadata_confidence",
    "keywords",
    "semantic_tags",
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
    asset_type: str = ""
    workflow_stage: str = ""
    label: str = ""


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
                file_id, internal_video_id, file_name, clean_title, editorial_title,
                original_title, alternate_titles, folder_path, speaker, preacher,
                ministry, main_theme, spiritual_themes, doctrine_topics,
                biblical_topics, bible_references, songs, worship_leaders,
                content_type, event_name, location, series_name, teaching_type,
                ai_summary, transcript_summary, keywords, semantic_tags
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                payload.get("file_id", ""),
                payload.get("internal_video_id", ""),
                payload.get("file_name", ""),
                payload.get("clean_title", ""),
                payload.get("editorial_title", ""),
                payload.get("original_title", ""),
                payload.get("alternate_titles", ""),
                payload.get("folder_path", ""),
                payload.get("speaker", ""),
                payload.get("preacher", ""),
                payload.get("ministry", ""),
                payload.get("main_theme", ""),
                payload.get("spiritual_themes", ""),
                payload.get("doctrine_topics", ""),
                payload.get("biblical_topics", ""),
                payload.get("bible_references", ""),
                payload.get("songs", ""),
                payload.get("worship_leaders", ""),
                payload.get("content_type", ""),
                payload.get("event_name", ""),
                payload.get("location", ""),
                payload.get("series_name", ""),
                payload.get("teaching_type", ""),
                payload.get("ai_summary", ""),
                payload.get("transcript_summary", ""),
                payload.get("keywords", ""),
                payload.get("semantic_tags", ""),
            ),
        )

    def update_christian_metadata(self, file_id: str, metadata: dict[str, Any]) -> VideoRecord | None:
        allowed = {key: metadata[key] for key in CHRISTIAN_METADATA_FIELDS if key in metadata}
        if not allowed:
            return self.get_video(file_id)

        for key, value in list(allowed.items()):
            if key == "metadata_confidence":
                allowed[key] = None if value in ("", None) else float(value)
            else:
                allowed[key] = str(value or "").strip()
        allowed["metadata_updated_at"] = self._now_sql_expr()

        assignments = ", ".join(f"{key} = ?" for key in allowed)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE file_id = ?", (file_id,)).fetchone()
            if not row:
                return None
            conn.execute(
                f"UPDATE videos SET {assignments} WHERE file_id = ?",
                [*allowed.values(), file_id],
            )
            updated = conn.execute("SELECT * FROM videos WHERE file_id = ?", (file_id,)).fetchone()
            assert updated is not None
            payload = VideoRecord.from_row(updated).to_dict()
            self._upsert_fts(conn, payload)
            self._sync_manual_lexicon_terms(conn, file_id, payload)
            conn.commit()
            return VideoRecord.from_row(updated)

    def update_workflow(self, file_id: str, workflow: dict[str, Any]) -> VideoRecord | None:
        asset_type = str(workflow.get("asset_type") or "raw").strip()
        workflow_stage = str(workflow.get("workflow_stage") or "digitized").strip()
        source_file_id = str(workflow.get("source_file_id") or "").strip()
        workflow_notes = str(workflow.get("workflow_notes") or "").strip()

        if asset_type not in ASSET_TYPES:
            raise ValueError("Invalid asset type")
        if workflow_stage not in WORKFLOW_STAGES:
            raise ValueError("Invalid workflow stage")
        if asset_type == "raw":
            source_file_id = ""
        if source_file_id == file_id:
            raise ValueError("A video cannot be its own source")

        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM videos WHERE file_id = ?", (file_id,)).fetchone():
                return None
            if source_file_id:
                source = conn.execute(
                    "SELECT asset_type FROM videos WHERE file_id = ?",
                    (source_file_id,),
                ).fetchone()
                if not source or source["asset_type"] != "raw":
                    raise ValueError("Source video must be an existing raw video")
            conn.execute(
                """
                UPDATE videos
                SET asset_type = ?, workflow_stage = ?, source_file_id = ?,
                    workflow_notes = ?, workflow_updated_at = datetime('now')
                WHERE file_id = ?
                """,
                (asset_type, workflow_stage, source_file_id, workflow_notes, file_id),
            )
            row = conn.execute("SELECT * FROM videos WHERE file_id = ?", (file_id,)).fetchone()
            conn.commit()
        return VideoRecord.from_row(row) if row else None

    def get_workflow_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            asset_rows = conn.execute(
                "SELECT asset_type, COUNT(*) AS count FROM videos GROUP BY asset_type"
            ).fetchall()
            stage_rows = conn.execute(
                "SELECT workflow_stage, COUNT(*) AS count FROM videos GROUP BY workflow_stage"
            ).fetchall()
            linked_cuts = conn.execute(
                """
                SELECT COUNT(*) FROM videos
                WHERE asset_type = 'cut' AND source_file_id IS NOT NULL AND source_file_id != ''
                """
            ).fetchone()[0]
            unlinked_cuts = conn.execute(
                """
                SELECT COUNT(*) FROM videos
                WHERE asset_type = 'cut' AND (source_file_id IS NULL OR source_file_id = '')
                """
            ).fetchone()[0]
        return {
            "assets": {row["asset_type"]: row["count"] for row in asset_rows},
            "stages": {row["workflow_stage"]: row["count"] for row in stage_rows},
            "linked_cuts": linked_cuts,
            "unlinked_cuts": unlinked_cuts,
        }

    def get_raw_video_options(self, exclude_file_id: str = "") -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT file_id, file_name, editorial_title, folder_path
                FROM videos
                WHERE asset_type = 'raw' AND file_id != ?
                ORDER BY COALESCE(NULLIF(editorial_title, ''), file_name) COLLATE NOCASE
                """,
                (exclude_file_id,),
            ).fetchall()
        return [
            {
                "file_id": row["file_id"],
                "label": row["editorial_title"] or row["file_name"],
                "folder_path": row["folder_path"] or "",
            }
            for row in rows
        ]

    def get_related_videos(self, file_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            current = conn.execute(
                "SELECT source_file_id, asset_type FROM videos WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            if not current:
                return {"source": None, "cuts": []}
            source = None
            if current["source_file_id"]:
                source = conn.execute(
                    "SELECT * FROM videos WHERE file_id = ?",
                    (current["source_file_id"],),
                ).fetchone()
            cuts = conn.execute(
                "SELECT * FROM videos WHERE source_file_id = ? ORDER BY file_name COLLATE NOCASE",
                (file_id,),
            ).fetchall()
        return {
            "source": VideoRecord.from_row(source).to_dict() if source else None,
            "cuts": [VideoRecord.from_row(row).to_dict() for row in cuts],
        }

    def get_video_labels(self, file_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT l.id, l.name, l.color
                FROM video_labels vl
                JOIN labels l ON l.id = vl.label_id
                WHERE vl.file_id = ?
                ORDER BY l.name COLLATE NOCASE
                """,
                (file_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_all_labels(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT l.id, l.name, l.color, COUNT(vl.file_id) AS video_count
                FROM labels l
                LEFT JOIN video_labels vl ON vl.label_id = l.id
                GROUP BY l.id
                ORDER BY l.name COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def set_video_labels(self, file_id: str, names: list[str]) -> list[dict[str, Any]] | None:
        clean_names: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_name in names:
            name = " ".join(str(raw_name).strip().split())
            normalized = name.casefold()
            if not name or normalized in seen:
                continue
            seen.add(normalized)
            clean_names.append((name, normalized))

        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM videos WHERE file_id = ?", (file_id,)).fetchone():
                return None
            conn.execute("DELETE FROM video_labels WHERE file_id = ?", (file_id,))
            for name, normalized in clean_names:
                conn.execute(
                    "INSERT OR IGNORE INTO labels(name, normalized_name) VALUES(?, ?)",
                    (name, normalized),
                )
                label_id = conn.execute(
                    "SELECT id FROM labels WHERE normalized_name = ?",
                    (normalized,),
                ).fetchone()[0]
                conn.execute(
                    "INSERT OR IGNORE INTO video_labels(file_id, label_id) VALUES(?, ?)",
                    (file_id, label_id),
                )
            conn.execute(
                "DELETE FROM labels WHERE id NOT IN (SELECT DISTINCT label_id FROM video_labels)"
            )
            conn.commit()
        return self.get_video_labels(file_id)

    def get_labels_map(self, file_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not file_ids:
            return {}
        placeholders = ",".join("?" for _ in file_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT vl.file_id, l.id, l.name, l.color
                FROM video_labels vl
                JOIN labels l ON l.id = vl.label_id
                WHERE vl.file_id IN ({placeholders})
                ORDER BY l.name COLLATE NOCASE
                """,
                file_ids,
            ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {file_id: [] for file_id in file_ids}
        for row in rows:
            result[row["file_id"]].append(
                {"id": row["id"], "name": row["name"], "color": row["color"]}
            )
        return result

    def get_video_lexicon_terms(self, file_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    vlt.id,
                    lt.category,
                    lt.term,
                    lt.description,
                    vlt.source,
                    vlt.confidence,
                    vlt.evidence,
                    vlt.created_at
                FROM video_lexicon_terms vlt
                JOIN lexicon_terms lt ON lt.id = vlt.term_id
                WHERE vlt.file_id = ?
                ORDER BY lt.category COLLATE NOCASE, lt.term COLLATE NOCASE
                """,
                (file_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _sync_manual_lexicon_terms(self, conn: sqlite3.Connection, file_id: str, payload: dict[str, Any]) -> None:
        field_categories = {
            "main_theme": "theme",
            "spiritual_themes": "theme",
            "doctrine_topics": "doctrine",
            "biblical_topics": "biblical_topic",
            "bible_references": "scripture",
            "songs": "song",
            "worship_leaders": "person",
            "speaker": "person",
            "preacher": "person",
            "ministry": "ministry",
            "event_name": "event",
            "location": "place",
            "content_type": "content_type",
            "keywords": "keyword",
            "semantic_tags": "semantic_tag",
        }
        conn.execute(
            """
            DELETE FROM video_lexicon_terms
            WHERE file_id = ?
              AND source = 'manual'
              AND term_id IN (
                SELECT id FROM lexicon_terms
                WHERE category IN ({})
              )
            """.format(",".join("?" for _ in sorted(set(field_categories.values())))),
            [file_id, *sorted(set(field_categories.values()))],
        )
        for field, category in field_categories.items():
            for term in _split_terms(str(payload.get(field) or "")):
                term_id = self._ensure_lexicon_term(conn, category, term)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO video_lexicon_terms(file_id, term_id, source, confidence, evidence)
                    VALUES(?, ?, 'manual', ?, ?)
                    """,
                    (file_id, term_id, payload.get("metadata_confidence"), field),
                )

    def _ensure_lexicon_term(self, conn: sqlite3.Connection, category: str, term: str) -> int:
        normalized = _normalize_term(term)
        conn.execute(
            """
            INSERT OR IGNORE INTO lexicon_terms(category, term, normalized_term)
            VALUES(?, ?, ?)
            """,
            (category, term, normalized),
        )
        row = conn.execute(
            "SELECT id FROM lexicon_terms WHERE category = ? AND normalized_term = ?",
            (category, normalized),
        ).fetchone()
        assert row is not None
        return int(row["id"])

    def _now_sql_expr(self) -> str:
        with self._connect() as conn:
            return str(conn.execute("SELECT datetime('now')").fetchone()[0])

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
        where, params = self._build_where(SearchFilters(**{**asdict(filters), "query": ""}))
        where_prefix = f"{where} AND" if where else "WHERE"

        count_sql = f"""
            SELECT COUNT(*) FROM videos
            {where_prefix} file_id IN (SELECT file_id FROM videos_fts WHERE videos_fts MATCH ?)
        """

        data_sql = f"""
            SELECT * FROM videos
            {where_prefix} file_id IN (SELECT file_id FROM videos_fts WHERE videos_fts MATCH ?)
            ORDER BY {sort_col} {direction}, file_name ASC
            LIMIT ? OFFSET ?
        """

        with self._connect() as conn:
            total = conn.execute(count_sql, [*params, query]).fetchone()[0]
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
                """
                (
                    file_name LIKE ?
                    OR folder_path LIKE ?
                    OR parent_folder LIKE ?
                    OR owner LIKE ?
                    OR editorial_title LIKE ?
                    OR original_title LIKE ?
                    OR alternate_titles LIKE ?
                    OR clean_title LIKE ?
                    OR speaker LIKE ?
                    OR preacher LIKE ?
                    OR ministry LIKE ?
                    OR main_theme LIKE ?
                    OR spiritual_themes LIKE ?
                    OR doctrine_topics LIKE ?
                    OR biblical_topics LIKE ?
                    OR bible_references LIKE ?
                    OR songs LIKE ?
                    OR content_type LIKE ?
                    OR event_name LIKE ?
                    OR transcript_summary LIKE ?
                    OR keywords LIKE ?
                    OR semantic_tags LIKE ?
                    OR file_id IN (
                        SELECT vl.file_id
                        FROM video_labels vl
                        JOIN labels l ON l.id = vl.label_id
                        WHERE l.name LIKE ?
                    )
                )
                """
            )
            params.extend([q] * 23)

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

        if filters.asset_type:
            clauses.append("asset_type = ?")
            params.append(filters.asset_type)

        if filters.workflow_stage:
            clauses.append("workflow_stage = ?")
            params.append(filters.workflow_stage)

        if filters.label:
            clauses.append(
                """
                file_id IN (
                    SELECT vl.file_id
                    FROM video_labels vl
                    JOIN labels l ON l.id = vl.label_id
                    WHERE l.normalized_name = ?
                )
                """
            )
            params.append(filters.label.casefold())

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


def _split_terms(value: str) -> list[str]:
    raw_terms = value.replace("\n", ";").replace("|", ";").split(";")
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_terms:
        term = raw.strip(" ,")
        if not term:
            continue
        key = _normalize_term(term)
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


def _normalize_term(value: str) -> str:
    return " ".join(value.casefold().strip().split())
