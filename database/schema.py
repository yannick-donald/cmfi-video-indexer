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
        ("editorial_title", "TEXT"),
        ("original_title", "TEXT"),
        ("alternate_titles", "TEXT"),
        ("speaker", "TEXT"),
        ("preacher", "TEXT"),
        ("ministry", "TEXT"),
        ("main_theme", "TEXT"),
        ("spiritual_themes", "TEXT"),
        ("doctrine_topics", "TEXT"),
        ("biblical_topics", "TEXT"),  # JSON or pipe-delimited
        ("bible_references", "TEXT"),  # extracted refs, e.g. \"Romans 8; John 3:16\"
        ("songs", "TEXT"),
        ("worship_leaders", "TEXT"),
        ("content_type", "TEXT"),
        ("event_name", "TEXT"),
        ("event_date", "TEXT"),
        ("location", "TEXT"),
        ("language", "TEXT"),
        ("audience", "TEXT"),
        ("series_name", "TEXT"),
        ("session_number", "TEXT"),
        ("teaching_type", "TEXT"),
        ("ai_summary", "TEXT"),
        ("transcript_status", "TEXT"),
        ("transcript_text_path", "TEXT"),
        ("transcript_summary", "TEXT"),
        ("manual_notes", "TEXT"),
        ("metadata_source", "TEXT"),
        ("metadata_confidence", "REAL"),
        ("metadata_updated_at", "TEXT"),
        ("keywords", "TEXT"),
        ("semantic_tags", "TEXT"),
        ("normalized_name", "TEXT"),
    ]:
        _add_column_if_missing(conn, "videos", col, sql_type)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_internal_video_id ON videos(internal_video_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_speaker ON videos(speaker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_preacher ON videos(preacher)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_main_theme ON videos(main_theme)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_content_type ON videos(content_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_event_name ON videos(event_name)")

    # 3) Christian lexicon and per-video term assignments.
    # Categories can include theme, doctrine, scripture, song, person, place,
    # ministry, event, book, topic, keyword, or any future editorial vocabulary.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lexicon_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            term TEXT NOT NULL,
            normalized_term TEXT NOT NULL,
            description TEXT,
            parent_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(category, normalized_term),
            FOREIGN KEY(parent_id) REFERENCES lexicon_terms(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lexicon_terms_category ON lexicon_terms(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lexicon_terms_term ON lexicon_terms(term COLLATE NOCASE)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS video_lexicon_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            term_id INTEGER NOT NULL,
            source TEXT DEFAULT 'manual',
            confidence REAL,
            evidence TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file_id, term_id, source),
            FOREIGN KEY(file_id) REFERENCES videos(file_id) ON DELETE CASCADE,
            FOREIGN KEY(term_id) REFERENCES lexicon_terms(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_video_lexicon_file_id ON video_lexicon_terms(file_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_video_lexicon_term_id ON video_lexicon_terms(term_id)")

    # 4) Full-text search index (FTS5) for enriched fields
    # Note: this is a standalone FTS table; we keep it synced from code on upsert.
    fts_sql = """
        CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
            file_id UNINDEXED,
            internal_video_id,
            file_name,
            clean_title,
            editorial_title,
            original_title,
            alternate_titles,
            folder_path,
            speaker,
            preacher,
            ministry,
            main_theme,
            spiritual_themes,
            doctrine_topics,
            biblical_topics,
            bible_references,
            songs,
            worship_leaders,
            content_type,
            event_name,
            location,
            series_name,
            teaching_type,
            ai_summary,
            transcript_summary,
            keywords,
            semantic_tags
        )
        """
    required_fts_cols = {
        "file_id",
        "internal_video_id",
        "file_name",
        "clean_title",
        "editorial_title",
        "original_title",
        "alternate_titles",
        "folder_path",
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
        "location",
        "series_name",
        "teaching_type",
        "ai_summary",
        "transcript_summary",
        "keywords",
        "semantic_tags",
    }
    existing_fts_cols = _table_columns(conn, "videos_fts") if _table_exists(conn, "videos_fts") else set()
    if existing_fts_cols and not required_fts_cols.issubset(existing_fts_cols):
        conn.execute("DROP TABLE videos_fts")
    conn.execute(fts_sql)
    conn.execute(
        """
        INSERT INTO videos_fts(
            file_id, internal_video_id, file_name, clean_title, editorial_title,
            original_title, alternate_titles, folder_path, speaker, preacher,
            ministry, main_theme, spiritual_themes, doctrine_topics,
            biblical_topics, bible_references, songs, worship_leaders,
            content_type, event_name, location, series_name, teaching_type,
            ai_summary, transcript_summary, keywords, semantic_tags
        )
        SELECT
            file_id, internal_video_id, file_name, clean_title, editorial_title,
            original_title, alternate_titles, folder_path, speaker, preacher,
            ministry, main_theme, spiritual_themes, doctrine_topics,
            biblical_topics, bible_references, songs, worship_leaders,
            content_type, event_name, location, series_name, teaching_type,
            ai_summary, transcript_summary, keywords, semantic_tags
        FROM videos
        WHERE file_id NOT IN (SELECT file_id FROM videos_fts)
        """
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'virtual table') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None
