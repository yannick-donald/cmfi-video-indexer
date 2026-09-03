from __future__ import annotations

import sqlite3
import json
import unicodedata
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from database.models import VideoRecord
from database.driver import Connection, make_driver
from database.schema import init_database
from metadata_cleaning.youtube_title import propose_title
from reporting.duplicates import normalize_for_comparison
from utils.internal_ids import extract_internal_id

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
    "watched",
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

# The internal ID is deliberately NOT preserved here: upsert_video always
# resolves it from the video_internal_ids registry, which is the authority, so
# writing it on every rescan is idempotent. Keeping it in this set meant a video
# indexed before the ID existed could never be repaired by a rescan.
PRESERVE_ON_RESCAN = CHRISTIAN_METADATA_FIELDS | {
    "asset_type",
    "workflow_stage",
    "source_file_id",
    "workflow_notes",
    "workflow_updated_at",
    "assigned_user_id",
    "assigned_user_email",
    "assigned_at",
    "assigned_by_email",
    "first_seen_at",
    "reviewed_at",
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
    tracking: str = ""
    assignee: str = ""


@dataclass(slots=True)
class SearchResult:
    items: list[VideoRecord]
    total: int
    page: int
    page_size: int
    total_pages: int


class VideoRepository:
    def __init__(self, db_path: Path, database_url: str = "") -> None:
        self.db_path = db_path
        # Vide = SQLite, le chemin de la production. Une URL postgresql://
        # bascule le pilote sans rien changer au reste de cette classe.
        self.driver = make_driver(database_url, db_path)
        if self.driver.dialect == "sqlite":
            init_database(db_path)

    def _connect(self) -> Connection:
        return self.driver.connect()

    def delete_demo_videos(self) -> int:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_id FROM videos WHERE file_id LIKE 'demo-%'"
            ).fetchall()
            file_ids = [str(row["file_id"]) for row in rows]
            if not file_ids:
                return 0

            placeholders = ", ".join("?" for _ in file_ids)
            conn.execute(
                f"UPDATE videos SET source_file_id = NULL WHERE source_file_id IN ({placeholders})",
                file_ids,
            )
            conn.execute(
                f"DELETE FROM videos_fts WHERE file_id IN ({placeholders})",
                file_ids,
            )
            conn.execute(
                f"DELETE FROM video_internal_ids WHERE file_id IN ({placeholders})",
                file_ids,
            )
            conn.execute(
                f"DELETE FROM videos WHERE file_id IN ({placeholders})",
                file_ids,
            )
            conn.execute(
                """
                DELETE FROM labels
                WHERE NOT EXISTS (
                    SELECT 1 FROM video_labels WHERE video_labels.label_id = labels.id
                )
                """
            )
            conn.commit()
        return len(file_ids)

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
        updates = ", ".join(
            f"{col}=excluded.{col}"
            for col in columns
            if col != "file_id" and col not in PRESERVE_ON_RESCAN
        )
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

    def _upsert_fts(self, conn: Connection, payload: dict[str, Any]) -> None:
        # FTS5 et tsvector n'ont pas la même forme : le pilote tranche.
        self.driver.fts_upsert(conn, payload)

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
        allowed["reviewed_at"] = self._now_sql_expr()

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
                    workflow_notes = ?, workflow_updated_at = datetime('now'),
                    reviewed_at = datetime('now')
                WHERE file_id = ?
                """,
                (asset_type, workflow_stage, source_file_id, workflow_notes, file_id),
            )
            row = conn.execute("SELECT * FROM videos WHERE file_id = ?", (file_id,)).fetchone()
            conn.commit()
        return VideoRecord.from_row(row) if row else None

    def auto_link_cuts_by_filename(self) -> dict[str, Any]:
        """
        Link cuts to their source using the internal ID written in the file name.

        An editor working outside the app names the cut "CHR-VID-000199 -
        Extrait.mp4"; the next scan reads that ID and attaches the cut to
        CHR-VID-000199 on its own.

        Deliberately conservative - it only ever fills a blank:

        - a video that already has a source is never touched;
        - a video whose workflow a human has saved is never touched, since
          workflow_updated_at is set only by update_workflow, i.e. only when
          somebody actually chose an asset type. Editing a title or a label does
          not count, so ordinary curation does not opt a video out;
        - the target must exist, be a different video, and be raw;
        - a video that is itself about to become a cut cannot be a source, which
          keeps the graph one level deep and rules out cycles.

        Returns the count and the pairs it created, for logging and reporting.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT file_id, file_name, internal_video_id, asset_type,
                       COALESCE(source_file_id, '') AS source_file_id,
                       COALESCE(workflow_updated_at, '') AS workflow_updated_at
                FROM videos
                """
            ).fetchall()

            by_internal_id = {
                str(row["internal_video_id"]): row
                for row in rows
                if row["internal_video_id"]
            }

            candidates = [
                row
                for row in rows
                if not row["source_file_id"] and not row["workflow_updated_at"]
            ]

            # Resolve every link against the state before any write, so the
            # outcome does not depend on iteration order.
            proposed: list[tuple[str, str, str]] = []
            for row in candidates:
                referenced = extract_internal_id(str(row["file_name"] or ""))
                if not referenced:
                    continue
                target = by_internal_id.get(referenced)
                if target is None or target["file_id"] == row["file_id"]:
                    continue
                if target["asset_type"] != "raw":
                    continue
                proposed.append((str(row["file_id"]), str(target["file_id"]), referenced))

            becoming_cuts = {file_id for file_id, _, _ in proposed}
            links = [
                (file_id, source_id, referenced)
                for file_id, source_id, referenced in proposed
                if source_id not in becoming_cuts
            ]

            for file_id, source_id, _ in links:
                conn.execute(
                    """
                    UPDATE videos
                    SET asset_type = 'cut', source_file_id = ?
                    WHERE file_id = ?
                    """,
                    (source_id, file_id),
                )
            if links:
                conn.commit()

        skipped_chain = len(proposed) - len(links)
        return {
            "linked": len(links),
            "skipped_chained_source": skipped_chain,
            "pairs": [
                {"file_id": file_id, "source_file_id": source_id, "source_internal_id": referenced}
                for file_id, source_id, referenced in links
            ],
        }

    def suggest_editorial_title(self, file_id: str) -> dict[str, Any] | None:
        """
        Propose a publishable title for one video, without saving anything.

        A cut inherits speaker, place and year from the raw video it came from,
        so its own name only has to carry the focus of the extract.
        """
        video = self.get_video(file_id)
        if not video:
            return None

        metadata = {
            "speaker": video.speaker,
            "preacher": video.preacher,
            "location": video.location,
            "event_date": video.event_date,
            "content_type": video.content_type,
            "session_number": video.session_number,
            "main_theme": video.main_theme,
        }

        source_proposal = None
        if video.asset_type == "cut" and video.source_file_id:
            source = self.get_video(video.source_file_id)
            if source:
                source_proposal = propose_title(
                    source.file_name,
                    metadata={
                        "speaker": source.speaker,
                        "preacher": source.preacher,
                        "location": source.location,
                        "event_date": source.event_date,
                        "content_type": source.content_type,
                        "main_theme": source.main_theme,
                    },
                )
                # A saved editorial title on the source is the best context there
                # is, so it outranks anything re-derived from its file name.
                if source.editorial_title.strip():
                    source_proposal.title = source.editorial_title.strip()

        proposal = propose_title(video.file_name, metadata=metadata, source=source_proposal)
        return {
            "file_id": file_id,
            "title": proposal.title,
            "confidence": proposal.confidence,
            "notes": proposal.notes,
            "current_title": video.editorial_title,
            "is_cut": video.asset_type == "cut",
            "source_title": source_proposal.title if source_proposal else "",
            "fields": {
                "speaker": proposal.speaker,
                "location": proposal.location,
                "event_date": proposal.event_date,
                "content_type": proposal.content_type,
                "session_number": proposal.session_number,
                "source_medium": proposal.source_medium,
            },
        }

    def _title_metadata(self, video: VideoRecord) -> dict[str, str]:
        return {
            "speaker": video.speaker,
            "preacher": video.preacher,
            "location": video.location,
            "event_date": video.event_date,
            "content_type": video.content_type,
            "session_number": video.session_number,
            "main_theme": video.main_theme,
        }

    def fill_missing_editorial_titles(
        self,
        *,
        apply: bool = False,
        include_partial: bool = True,
    ) -> dict[str, Any]:
        """
        Propose a title for every video that has none.

        Never overwrites: a video with an editorial title is left alone, whoever
        wrote it. With apply=False nothing is written and the caller gets the
        exact list it would have written, so a mass edit can be reviewed first.

        Raw videos are resolved before cuts, so a cut inherits the title its
        source is about to receive rather than the one it had a moment ago.
        """
        videos = list(self.iter_videos())
        by_file_id = {video.file_id: video for video in videos}
        proposals: dict[str, Any] = {}

        for video in videos:
            if video.asset_type != "cut":
                proposals[video.file_id] = propose_title(
                    video.file_name, metadata=self._title_metadata(video)
                )

        for video in videos:
            if video.asset_type != "cut":
                continue
            source_proposal = None
            source = by_file_id.get(video.source_file_id) if video.source_file_id else None
            if source is not None:
                base = proposals.get(source.file_id) or propose_title(
                    source.file_name, metadata=self._title_metadata(source)
                )
                # Copy before overriding: the source keeps its own proposal.
                source_proposal = replace(base)
                if source.editorial_title.strip():
                    source_proposal.title = source.editorial_title.strip()
            proposals[video.file_id] = propose_title(
                video.file_name, metadata=self._title_metadata(video), source=source_proposal
            )

        selected: list[tuple[VideoRecord, Any]] = []
        skipped_existing = 0
        skipped_no_title = 0
        skipped_partial = 0
        for video in videos:
            if video.editorial_title.strip():
                skipped_existing += 1
                continue
            proposal = proposals[video.file_id]
            if not proposal.title:
                skipped_no_title += 1
                continue
            if proposal.confidence != "high" and not include_partial:
                skipped_partial += 1
                continue
            selected.append((video, proposal))

        if apply and selected:
            with self._connect() as conn:
                for video, proposal in selected:
                    conn.execute(
                        "UPDATE videos SET editorial_title = ? WHERE file_id = ?",
                        (proposal.title, video.file_id),
                    )
                    # reviewed_at is deliberately untouched: a generated title is
                    # not a human review, and marking it as one would empty the
                    # "nouvelles vidéos" queue without anyone having looked.
                    row = conn.execute(
                        "SELECT * FROM videos WHERE file_id = ?", (video.file_id,)
                    ).fetchone()
                    if row:
                        self._upsert_fts(conn, VideoRecord.from_row(row).to_dict())
                conn.commit()

        return {
            "applied": bool(apply),
            "eligible": len(selected),
            "high": sum(1 for _, proposal in selected if proposal.confidence == "high"),
            "partial": sum(1 for _, proposal in selected if proposal.confidence == "partial"),
            "skipped_existing_title": skipped_existing,
            "skipped_no_proposal": skipped_no_title,
            "skipped_partial_excluded": skipped_partial,
            "sample": [
                {
                    "internal_video_id": video.internal_video_id,
                    "file_name": video.file_name,
                    "title": proposal.title,
                    "confidence": proposal.confidence,
                }
                for video, proposal in selected[:15]
            ],
        }

    def get_unlinked_cuts(self, limit: int = 50) -> list[dict[str, Any]]:
        """Cut videos still waiting to be attached to the raw video they came from."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT file_id, internal_video_id, file_name, editorial_title,
                       folder_path, parent_folder, speaker, preacher, event_name,
                       location, event_date, modified_at, created_at
                FROM videos
                WHERE asset_type = 'cut' AND COALESCE(source_file_id, '') = ''
                ORDER BY COALESCE(NULLIF(first_seen_at, ''), modified_at) DESC,
                         file_name COLLATE NOCASE
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def suggest_sources_for(self, file_id: str, limit: int = 3) -> list[dict[str, Any]]:
        """
        Rank the raw videos this cut most plausibly came from.

        Scored on what this library actually knows. Duration would be the
        strongest signal - a cut is always shorter than its source - but ffprobe
        is disabled in the hosted deployment, so durations are absent and the
        ranking leans on names, folders and shared context instead. Every
        candidate carries the reasons it was picked, so a human can judge rather
        than trust a bare number.
        """
        cut = self.get_video(file_id)
        if not cut:
            return []

        cut_tokens = set(normalize_for_comparison(cut.file_name).split())
        cut_title_tokens = set(normalize_for_comparison(cut.editorial_title).split())
        cut_year = (cut.event_date or cut.modified_at or "")[:4]

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT file_id, internal_video_id, file_name, editorial_title,
                       folder_path, parent_folder, speaker, preacher, event_name,
                       location, event_date, modified_at, file_size, duration_seconds
                FROM videos
                WHERE asset_type = 'raw' AND file_id != ?
                """,
                (file_id,),
            ).fetchall()

        scored: list[tuple[float, list[str], dict[str, Any]]] = []
        for row in rows:
            score = 0.0
            reasons: list[str] = []

            source_tokens = set(normalize_for_comparison(row["file_name"]).split())
            shared = (cut_tokens | cut_title_tokens) & source_tokens
            if shared and source_tokens:
                overlap = len(shared) / len(cut_tokens | source_tokens)
                if overlap > 0.15:
                    score += overlap * 5
                    reasons.append(f"mots communs : {', '.join(sorted(shared)[:4])}")

            if cut.folder_path and row["folder_path"] == cut.folder_path:
                score += 2
                reasons.append("même dossier")
            elif cut.parent_folder and row["parent_folder"] == cut.parent_folder:
                score += 1
                reasons.append("même dossier parent")

            for field, label in (("speaker", "intervenant"), ("preacher", "prédicateur"),
                                 ("event_name", "événement"), ("location", "lieu")):
                mine = (getattr(cut, field) or "").strip().casefold()
                theirs = (row[field] or "").strip().casefold()
                if mine and mine == theirs:
                    score += 1.5
                    reasons.append(f"même {label}")

            source_year = (row["event_date"] or row["modified_at"] or "")[:4]
            if cut_year and source_year and cut_year == source_year:
                score += 0.5
                reasons.append(f"même année ({cut_year})")

            # A source must predate its cut; treat the reverse as a warning
            # rather than a veto, since Drive timestamps are only a proxy.
            if cut.modified_at and row["modified_at"] and row["modified_at"] > cut.modified_at:
                score -= 0.5

            if cut.duration_seconds and row["duration_seconds"]:
                if row["duration_seconds"] > cut.duration_seconds:
                    score += 1.5
                    reasons.append("source plus longue")
                else:
                    score -= 2

            if score > 0.5 and reasons:
                scored.append((score, reasons, dict(row)))

        scored.sort(key=lambda item: -item[0])
        return [
            {
                "file_id": row["file_id"],
                "internal_video_id": row["internal_video_id"] or "",
                "label": row["editorial_title"] or row["file_name"],
                "file_name": row["file_name"],
                "folder_path": row["folder_path"] or "",
                "score": round(score, 2),
                "reasons": reasons[:4],
            }
            for score, reasons, row in scored[: max(1, min(limit, 10))]
        ]

    def link_cut_source(self, file_id: str, source_file_id: str) -> VideoRecord | None:
        """
        Attach a cut to its source, keeping its current production stage.

        Routed through update_workflow so the same rules apply as a manual edit:
        the source must be an existing raw video, and a video cannot be its own
        source. It also stamps workflow_updated_at, which marks the choice as
        human and puts the video out of reach of the automatic linker.
        """
        video = self.get_video(file_id)
        if not video:
            return None
        return self.update_workflow(
            file_id,
            {
                "asset_type": "cut",
                "workflow_stage": video.workflow_stage,
                "source_file_id": source_file_id,
                "workflow_notes": video.workflow_notes,
            },
        )

    def assign_video(
        self,
        file_id: str,
        user_id: int | None,
        user_email: str,
        *,
        assigned_by_email: str,
    ) -> VideoRecord | None:
        """
        Designate who is responsible for a video, or clear the assignment.

        The e-mail is stored alongside the id so that lists and exports need no
        join, and so the record still reads correctly if the account is later
        removed. Passing user_id=None unassigns.
        """
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM videos WHERE file_id = ?", (file_id,)).fetchone():
                return None
            if user_id is None:
                conn.execute(
                    """
                    UPDATE videos
                    SET assigned_user_id = NULL, assigned_user_email = '',
                        assigned_at = '', assigned_by_email = ''
                    WHERE file_id = ?
                    """,
                    (file_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE videos
                    SET assigned_user_id = ?, assigned_user_email = ?,
                        assigned_at = datetime('now'), assigned_by_email = ?
                    WHERE file_id = ?
                    """,
                    (user_id, user_email, assigned_by_email, file_id),
                )
            row = conn.execute("SELECT * FROM videos WHERE file_id = ?", (file_id,)).fetchone()
            conn.commit()
        return VideoRecord.from_row(row) if row else None

    def get_user_display_names(self) -> dict[str, str]:
        """
        Map e-mail -> name for everyone with an account.

        The name is resolved at display time rather than copied onto each video,
        so renaming somebody updates every screen at once instead of leaving
        stale copies behind.
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT email, full_name FROM users").fetchall()
        return {
            row["email"]: (row["full_name"] or "").strip() or row["email"]
            for row in rows
        }

    def get_assignment_stats(self) -> dict[str, Any]:
        """Workload per person, so the admin can see how work is spread."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT assigned_user_email AS email, COUNT(*) AS total,
                       SUM(CASE WHEN workflow_stage = 'published' THEN 1 ELSE 0 END) AS published
                FROM videos
                WHERE COALESCE(assigned_user_email, '') != ''
                GROUP BY assigned_user_email
                ORDER BY total DESC
                """
            ).fetchall()
            unassigned = conn.execute(
                "SELECT COUNT(*) FROM videos WHERE COALESCE(assigned_user_id, 0) = 0"
            ).fetchone()[0]
        names = self.get_user_display_names()
        return {
            "per_user": [
                {**dict(row), "display_name": names.get(row["email"], row["email"])}
                for row in rows
            ],
            "unassigned": unassigned,
        }

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

    def get_raw_video_options(
        self,
        exclude_file_id: str = "",
        query: str = "",
        limit: int = 20,
    ) -> list[dict[str, str]]:
        """
        Candidate source videos for a cut, as a ranked and bounded list.

        Returning every raw video was unusable: because new videos default to
        'raw', that list was effectively the whole library. Callers now search -
        by internal ID, title, file name or folder - and get the best matches
        only. An empty query returns the most recently modified videos, which is
        a far more useful default than the alphabetical head of the library.
        """
        term = query.strip()
        limit = max(1, min(limit, 50))
        params: dict[str, Any] = {"exclude": exclude_file_id, "limit": limit}

        if term:
            params["like"] = f"%{term}%"
            params["exact"] = term
            where = """
                AND (
                    internal_video_id LIKE :like
                    OR file_name LIKE :like
                    OR editorial_title LIKE :like
                    OR clean_title LIKE :like
                    OR folder_path LIKE :like
                )
            """
            # An exact internal-ID hit is what someone pasting an ID expects to
            # see first; everything else falls back to alphabetical order.
            order = """
                CASE
                    WHEN internal_video_id = :exact COLLATE NOCASE THEN 0
                    WHEN internal_video_id LIKE :like THEN 1
                    WHEN editorial_title LIKE :like THEN 2
                    WHEN file_name LIKE :like THEN 3
                    ELSE 4
                END,
                COALESCE(NULLIF(editorial_title, ''), file_name) COLLATE NOCASE
            """
        else:
            where = ""
            order = "modified_at DESC, file_name COLLATE NOCASE"

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT file_id, internal_video_id, file_name, editorial_title,
                       folder_path, modified_at
                FROM videos
                WHERE asset_type = 'raw' AND file_id != :exclude
                {where}
                ORDER BY {order}
                LIMIT :limit
                """,
                params,
            ).fetchall()
        return [
            {
                "file_id": row["file_id"],
                "internal_video_id": row["internal_video_id"] or "",
                "label": row["editorial_title"] or row["file_name"],
                "file_name": row["file_name"] or "",
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

    def set_video_labels(
        self,
        file_id: str,
        names: list[str],
        *,
        user_id: int | None,
        user_email: str,
    ) -> list[dict[str, Any]] | None:
        clean_names: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_name in names:
            name = " ".join(str(raw_name).strip().split())
            normalized = _normalize_label(name)
            if not name or normalized in seen:
                continue
            seen.add(normalized)
            clean_names.append((name, normalized))

        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM videos WHERE file_id = ?", (file_id,)).fetchone():
                return None
            before_rows = conn.execute(
                """
                SELECT l.name
                FROM video_labels vl
                JOIN labels l ON l.id = vl.label_id
                WHERE vl.file_id = ?
                ORDER BY l.name COLLATE NOCASE
                """,
                (file_id,),
            ).fetchall()
            before = [str(row["name"]) for row in before_rows]
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
            after = [name for name, _ in clean_names]
            before_keys = {_normalize_label(name): name for name in before}
            after_keys = {_normalize_label(name): name for name in after}
            if set(before_keys) != set(after_keys):
                conn.execute(
                    """
                    INSERT INTO video_label_history(
                        file_id, user_id, user_email, before_labels, after_labels,
                        added_labels, removed_labels
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        user_id,
                        user_email,
                        json.dumps(before, ensure_ascii=False),
                        json.dumps(after, ensure_ascii=False),
                        json.dumps(
                            [after_keys[key] for key in after_keys.keys() - before_keys.keys()],
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            [before_keys[key] for key in before_keys.keys() - after_keys.keys()],
                            ensure_ascii=False,
                        ),
                    ),
                )
            conn.execute(
                "UPDATE videos SET reviewed_at = datetime('now') WHERE file_id = ?",
                (file_id,),
            )
            conn.commit()
        return self.get_video_labels(file_id)

    def get_label_history(self, file_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_email, before_labels, after_labels,
                       added_labels, removed_labels, created_at
                FROM video_label_history
                WHERE file_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (file_id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "user_email": row["user_email"],
                "before_labels": json.loads(row["before_labels"]),
                "after_labels": json.loads(row["after_labels"]),
                "added_labels": json.loads(row["added_labels"]),
                "removed_labels": json.loads(row["removed_labels"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_latest_label_edits(self, file_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not file_ids:
            return {}
        placeholders = ",".join("?" for _ in file_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT h.file_id, h.user_email, h.created_at
                FROM video_label_history h
                JOIN (
                    SELECT file_id, MAX(id) AS max_id
                    FROM video_label_history
                    WHERE file_id IN ({placeholders})
                    GROUP BY file_id
                ) latest ON latest.max_id = h.id
                """,
                file_ids,
            ).fetchall()
        return {
            row["file_id"]: {
                "user_email": row["user_email"],
                "created_at": row["created_at"],
            }
            for row in rows
        }

    def get_drive_folder_setting(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT key, value, updated_by_email, updated_at
                FROM app_settings
                WHERE key IN (
                    'drive_folder_id', 'drive_folder_name', 'drive_folder_url',
                    'drive_last_scan_at', 'drive_last_scan_status'
                )
                """
            ).fetchall()
        if not rows:
            return None
        values = {row["key"]: row["value"] for row in rows}
        folder_rows = [
            row
            for row in rows
            if row["key"] in {"drive_folder_id", "drive_folder_name", "drive_folder_url"}
        ]
        latest = max(folder_rows or rows, key=lambda row: row["updated_at"] or "")
        return {
            "folder_id": values.get("drive_folder_id", ""),
            "folder_name": values.get("drive_folder_name", ""),
            "folder_url": values.get("drive_folder_url", ""),
            "last_scan_at": values.get("drive_last_scan_at", ""),
            "last_scan_status": values.get("drive_last_scan_status", ""),
            "updated_by_email": latest["updated_by_email"] or "",
            "updated_at": latest["updated_at"] or "",
        }

    def set_drive_folder_setting(
        self,
        *,
        folder_id: str,
        folder_name: str,
        folder_url: str,
        user_email: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            for key, value in (
                ("drive_folder_id", folder_id),
                ("drive_folder_name", folder_name),
                ("drive_folder_url", folder_url),
            ):
                conn.execute(
                    """
                    INSERT INTO app_settings(key, value, updated_by_email, updated_at)
                    VALUES(?, ?, ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_by_email = excluded.updated_by_email,
                        updated_at = excluded.updated_at
                    """,
                    (key, value, user_email),
                )
            conn.commit()
        return self.get_drive_folder_setting() or {}

    def record_drive_scan(self, *, status: str, scanned_at: str) -> None:
        with self._connect() as conn:
            for key, value in (
                ("drive_last_scan_status", status),
                ("drive_last_scan_at", scanned_at),
            ):
                conn.execute(
                    """
                    INSERT INTO app_settings(key, value, updated_at)
                    VALUES(?, ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, value),
                )
            conn.commit()

    def get_scan_state(self) -> dict[str, Any] | None:
        """Last known progress of the web-triggered Drive scan, if any."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'drive_scan_state'"
            ).fetchone()
        if not row or not row["value"]:
            return None
        try:
            state = json.loads(str(row["value"]))
        except json.JSONDecodeError:
            return None
        return state if isinstance(state, dict) else None

    def set_scan_state(self, state: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES('drive_scan_state', ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (json.dumps(state),),
            )
            conn.commit()

    def get_tracking_stats(self) -> dict[str, int]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN reviewed_at IS NULL OR reviewed_at = '' THEN 1 ELSE 0 END) AS new_count,
                    SUM(CASE WHEN NOT EXISTS (
                        SELECT 1 FROM video_labels vl WHERE vl.file_id = videos.file_id
                    ) THEN 1 ELSE 0 END) AS missing_labels,
                    SUM(CASE WHEN asset_type = 'cut'
                        AND (source_file_id IS NULL OR source_file_id = '') THEN 1 ELSE 0 END
                    ) AS unlinked_cuts,
                    SUM(CASE WHEN
                        reviewed_at IS NULL OR reviewed_at = ''
                        OR editorial_title IS NULL OR editorial_title = ''
                        OR main_theme IS NULL OR main_theme = ''
                        OR (
                            (speaker IS NULL OR speaker = '')
                            AND (preacher IS NULL OR preacher = '')
                        )
                        OR NOT EXISTS (
                            SELECT 1 FROM video_labels vl WHERE vl.file_id = videos.file_id
                        )
                    THEN 1 ELSE 0 END) AS incomplete
                FROM videos
                """
            ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}

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
        if use_fts and _build_fts_query(filters.query):
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
        query = _build_fts_query(filters.query)
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
                    """
                ).fetchall()
            ]
            extensions = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT file_extension FROM videos
                    WHERE file_extension IS NOT NULL AND file_extension != ''
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
                    """
                ).fetchall()
            ]
        # Tri insensible à la casse fait ici, et non en SQL : PostgreSQL
        # rejette une expression de tri absente de la sélection quand la requête
        # est DISTINCT. Trier en Python donne le même ordre sur les deux moteurs.
        folders = sorted(folders, key=str.casefold)
        extensions = sorted(extensions, key=str.casefold)
        shared_drives = sorted(shared_drives, key=str.casefold)

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

    def iter_videos(
        self,
        filters: SearchFilters | None = None,
        *,
        sort_by: str = "folder_path",
        sort_dir: str = "asc",
        batch_size: int = 500,
    ) -> Iterator[VideoRecord]:
        """
        Stream every video matching the filters, without pagination.

        Used by the report exporter, which must not hold the whole library in
        memory at once.
        """
        where, params = self._build_where(filters or SearchFilters())
        sort_col = SORT_COLUMNS.get(sort_by, SORT_COLUMNS["file_name"])
        direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
        sql = f"SELECT * FROM videos {where} ORDER BY {sort_col} {direction}, file_name ASC"

        conn = self._connect()
        try:
            cursor = conn.execute(sql, params)
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    yield VideoRecord.from_row(row)
        finally:
            conn.close()

    def get_duplicate_candidates(self) -> list[dict[str, Any]]:
        """Minimal columns needed to detect duplicates, for the whole library."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT file_id, file_name, folder_path, drive_url, internal_video_id,
                       file_size, duration_seconds, modified_at
                FROM videos
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_folder_summary(self) -> list[dict[str, Any]]:
        """Per-folder counts and sizes, deepest-first by path for readability."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(NULLIF(folder_path, ''), '(racine)') AS folder_path,
                    COALESCE(NULLIF(parent_folder, ''), '') AS parent_folder,
                    COALESCE(NULLIF(shared_drive_name, ''), '') AS shared_drive_name,
                    COUNT(*) AS video_count,
                    COALESCE(SUM(file_size), 0) AS total_bytes,
                    COALESCE(SUM(duration_seconds), 0) AS total_duration_seconds,
                    SUM(CASE WHEN asset_type = 'raw' THEN 1 ELSE 0 END) AS raw_count,
                    SUM(CASE WHEN asset_type = 'cut' THEN 1 ELSE 0 END) AS cut_count,
                    MIN(NULLIF(modified_at, '')) AS first_modified_at,
                    MAX(NULLIF(modified_at, '')) AS last_modified_at
                FROM videos
                GROUP BY folder_path, parent_folder, shared_drive_name
                ORDER BY folder_path COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_error_records(self) -> list[dict[str, Any]]:
        """Videos the scanner could not fully process."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT file_id, file_name, folder_path, drive_url, scan_status,
                       error_message, last_scanned_at
                FROM videos
                WHERE (error_message IS NOT NULL AND error_message != '')
                   OR (scan_status IS NOT NULL AND scan_status NOT IN ('indexed', ''))
                ORDER BY last_scanned_at DESC, file_name COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_scan_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT started_at, finished_at, status, videos_found, errors
                FROM scan_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _build_where(self, filters: SearchFilters) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if filters.query.strip():
            q = f"%{filters.query.strip()}%"
            clauses.append(
                """
                (
                    internal_video_id LIKE ?
                    OR file_name LIKE ?
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
            params.extend([q] * 24)

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
            params.append(_normalize_label(filters.label))

        if filters.assignee == "__none__":
            clauses.append("COALESCE(assigned_user_id, 0) = 0")
        elif filters.assignee:
            clauses.append("assigned_user_email = ?")
            params.append(filters.assignee.strip().casefold())

        if filters.tracking == "new":
            clauses.append("(reviewed_at IS NULL OR reviewed_at = '')")
        elif filters.tracking == "missing_labels":
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM video_labels vl WHERE vl.file_id = videos.file_id)"
            )
        elif filters.tracking == "unlinked_cut":
            clauses.append(
                "asset_type = 'cut' AND (source_file_id IS NULL OR source_file_id = '')"
            )
        elif filters.tracking == "incomplete":
            clauses.append(
                """
                (
                    reviewed_at IS NULL OR reviewed_at = ''
                    OR editorial_title IS NULL OR editorial_title = ''
                    OR main_theme IS NULL OR main_theme = ''
                    OR (
                        (speaker IS NULL OR speaker = '')
                        AND (preacher IS NULL OR preacher = '')
                    )
                    OR NOT EXISTS (
                        SELECT 1 FROM video_labels vl WHERE vl.file_id = videos.file_id
                    )
                )
                """
            )

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


def _build_fts_query(raw: str) -> str:
    """
    Turn free user input into a safe FTS5 MATCH expression.

    FTS5 treats -, ", *, :, ( ) and the bare words AND/OR/NOT/NEAR as query
    syntax, so passing user text through unchanged makes ordinary searches raise
    OperationalError: "Jean-Baptiste" was read as a column filter, "2026-01-12"
    as a subtraction, an odd quote as an unterminated string.

    Each whitespace-separated token is wrapped in double quotes - which makes it
    a literal phrase - with any inner quote doubled to escape it. Tokens are
    joined by spaces, FTS5's implicit AND, so every term must still match.

    Returns "" when nothing searchable remains, letting the caller fall back to
    the LIKE search rather than issuing an empty MATCH.
    """
    tokens = [token for token in raw.strip().split() if token.strip('"')]
    if not tokens:
        return ""
    return " ".join('"' + token.replace('"', '""') + '"' for token in tokens)


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


def _normalize_label(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split())


def _normalize_term(value: str) -> str:
    return " ".join(value.casefold().strip().split())
