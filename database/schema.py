from __future__ import annotations

import sqlite3
from pathlib import Path


BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    drive_url TEXT,
    folder_path TEXT,
    parent_folder TEXT,
    file_size INTEGER DEFAULT 0,
    mime_type TEXT,
    owner TEXT,
    created_at TEXT,
    modified_at TEXT,
    shared_drive_name TEXT,
    duration_seconds REAL,
    width INTEGER,
    height INTEGER,
    resolution TEXT,
    fps REAL,
    video_codec TEXT,
    audio_codec TEXT,
    bitrate INTEGER,
    aspect_ratio TEXT,
    orientation TEXT,
    has_audio INTEGER,
    container_format TEXT,
    file_extension TEXT,
    scan_status TEXT DEFAULT 'indexed',
    last_scanned_at TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_videos_file_name ON videos(file_name);
CREATE INDEX IF NOT EXISTS idx_videos_folder_path ON videos(folder_path);
CREATE INDEX IF NOT EXISTS idx_videos_extension ON videos(file_extension);
CREATE INDEX IF NOT EXISTS idx_videos_resolution ON videos(resolution);
CREATE INDEX IF NOT EXISTS idx_videos_modified_at ON videos(modified_at);
CREATE INDEX IF NOT EXISTS idx_videos_file_size ON videos(file_size);
CREATE INDEX IF NOT EXISTS idx_videos_duration ON videos(duration_seconds);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    videos_found INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0
);
"""


def init_database(db_path: Path) -> None:
    """
    Initialize and migrate the SQLite database in a backwards-compatible way.
    This keeps existing installs working while adding new capabilities.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(BASE_SCHEMA_SQL)
        _migrate(conn)
        conn.commit()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}  # (cid, name, type, notnull, dflt_value, pk)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, col: str, sql_type: str) -> None:
    cols = _table_columns(conn, table)
    if col in cols:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}")


def _migrate(conn: sqlite3.Connection) -> None:
    # 1) Persistent internal IDs
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS video_internal_ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL UNIQUE,
            internal_video_id TEXT UNIQUE,
            created_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_video_internal_ids_file_id ON video_internal_ids(file_id)")

    _add_column_if_missing(conn, "videos", "internal_video_id", "TEXT")

    # 2) Christian semantic enrichment fields (additive)
    for col, sql_type in [
        ("clean_title", "TEXT"),
        ("speaker", "TEXT"),
        ("ministry", "TEXT"),
        ("main_theme", "TEXT"),
        ("biblical_topics", "TEXT"),  # JSON or pipe-delimited
        ("bible_references", "TEXT"),  # extracted refs, e.g. \"Romans 8; John 3:16\"
        ("teaching_type", "TEXT"),
        ("ai_summary", "TEXT"),
        ("keywords", "TEXT"),
        ("semantic_tags", "TEXT"),
        ("normalized_name", "TEXT"),
    ]:
        _add_column_if_missing(conn, "videos", col, sql_type)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_internal_video_id ON videos(internal_video_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_speaker ON videos(speaker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_main_theme ON videos(main_theme)")

    # 3) Full-text search index (FTS5) for enriched fields
    # Note: this is a standalone FTS table; we keep it synced from code on upsert.
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
            file_id UNINDEXED,
            internal_video_id,
            file_name,
            clean_title,
            folder_path,
            speaker,
            ministry,
            main_theme,
            biblical_topics,
            bible_references,
            teaching_type,
            ai_summary,
            keywords,
            semantic_tags
        )
        """
    )
