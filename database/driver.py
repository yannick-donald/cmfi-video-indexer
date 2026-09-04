"""Pilote de base : le même code tourne sur SQLite ou sur PostgreSQL.

La production tourne sur SQLite. La branche UAT fait tourner ce même code
contre PostgreSQL + pgvector, pour pouvoir comparer les deux avant toute
bascule. `DATABASE_URL` choisit le moteur ; rien d'autre ne change.

Le reste du dépôt continue d'écrire du SQL avec des marqueurs « ? » et de lire
les lignes par nom comme par position. C'est ce module qui absorbe l'écart.
"""

from __future__ import annotations

import re
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence


def _to_pyformat(sql: str) -> str:
    """Traduit les marqueurs « ? » en « %s » pour psycopg.

    Un simple remplacement casserait sur un « ? » situé dans un littéral SQL,
    et sur un « % » littéral que psycopg interpréterait comme un marqueur. On
    parcourt donc la chaîne en suivant l'état « dans un littéral ou non ».
    L'audit du dépôt n'a trouvé aucun des deux cas, mais un futur SQL le
    pourrait, et la panne serait silencieuse.
    """
    out: list[str] = []
    in_literal = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_literal:
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    out.append("''")
                    i += 2
                    continue
                in_literal = False
            # Un « % » doit être doublé même dans un littéral : psycopg analyse
            # toute la requête, littéraux compris. Un « ? » en revanche y reste
            # un caractère ordinaire.
            out.append("%%" if ch == "%" else ch)
        elif ch == "'":
            in_literal = True
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# Horodatage UTC au format texte, identique à celui de SQLite. Les colonnes de
# dates sont en TEXT : si les deux moteurs ne produisent pas la même chaîne,
# les comparaisons de dates divergent sans rien signaler.
FTS_FIELDS = [
    "internal_video_id", "file_name", "clean_title", "editorial_title",
    "original_title", "alternate_titles", "folder_path", "speaker", "preacher",
    "ministry", "main_theme", "spiritual_themes", "doctrine_topics",
    "biblical_topics", "bible_references", "songs", "worship_leaders",
    "content_type", "event_name", "location", "series_name", "teaching_type",
    "ai_summary", "transcript_summary", "keywords", "semantic_tags",
]


PG_NOW = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"


def to_postgres(sql: str) -> str:
    """Traduit en PostgreSQL du SQL écrit pour SQLite.

    L'audit du dépôt n'a relevé que cinq écarts, tous sans ambiguïté. Les
    traduire ici plutôt que dans les 50 sites d'appel garde `repository.py`
    — qui tourne en production — quasiment intact, et met toute la logique de
    dialecte à un seul endroit qu'on peut tester.

    Ce qui n'est PAS traité ici, faute d'en avoir besoin : `strftime`, dont les
    8 usages sont du Python et non du SQL, et `sqlite_master`, qui n'apparaît
    que dans le chemin de migration SQLite.
    """
    # 1) Horodatage.
    sql = sql.replace("datetime('now')", PG_NOW)

    # 2) Tri insensible à la casse (29 sites, surtout des ORDER BY de titres).
    #
    # PostgreSQL refuse toute expression de tri absente de la sélection quand la
    # requête est DISTINCT — et il compte `LOWER(x)` comme `x COLLATE "…"` parmi
    # ces expressions. Les trois requêtes DISTINCT concernées trient donc en
    # Python (voir `get_filter_options`), ce qui donne en prime un ordre
    # rigoureusement identique sur les deux moteurs. Ici, `LOWER()` suffit.
    sql = re.sub(r'([\w".]+)\s+COLLATE\s+NOCASE', r"LOWER(\1)", sql, flags=re.IGNORECASE)

    # 3) Recherche plein texte : FTS5 d'un côté, tsvector de l'autre.
    sql = re.sub(
        r"videos_fts\s+WHERE\s+videos_fts\s+MATCH\s+\?",
        "videos_fts WHERE document @@ websearch_to_tsquery('french', ?)",
        sql,
        flags=re.IGNORECASE,
    )

    # 4) Insertion idempotente.
    if re.search(r"\bINSERT\s+OR\s+IGNORE\b", sql, re.IGNORECASE):
        sql = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT", sql, flags=re.IGNORECASE)
        if "ON CONFLICT" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    # 5) Marqueurs de paramètres — toujours en dernier.
    return _to_pyformat(sql)


class Row(dict):
    """Ligne lisible par nom et par position, comme `sqlite3.Row`.

    Le dépôt lit 182 fois `row["colonne"]` et 7 fois `fetchone()[0]`. psycopg
    ne propose pas les deux à la fois : `dict_row` perd l'index, `tuple_row`
    perd le nom. On rétablit les deux.
    """

    __slots__ = ("_values",)

    def __init__(self, keys: Sequence[str], values: Sequence[Any]) -> None:
        super().__init__(zip(keys, values))
        self._values = tuple(values)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        if isinstance(key, slice):
            return self._values[key]
        return super().__getitem__(key)


def _row_factory(cursor: Any) -> Any:
    """Fabrique de lignes pour psycopg, construite sur la description du curseur."""
    names = [d.name for d in cursor.description] if cursor.description else []

    def make(values: Sequence[Any]) -> Row:
        return Row(names, values)

    return make


class Connection:
    """Enveloppe une connexion DB-API pour préserver les habitudes du dépôt.

    Utilisable avec `with` (56 sites) comme sans (1 site, l'itération par lots
    de `iter_videos`). À la sortie du bloc : commit si tout s'est bien passé,
    rollback sinon, puis fermeture.
    """

    def __init__(self, raw: Any, dialect: str) -> None:
        self._raw = raw
        self._dialect = dialect

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        if self._dialect == "postgresql":
            sql = to_postgres(sql)
        return self._raw.execute(sql, tuple(params) if params else ())

    def executemany(self, sql: str, seq: Any) -> Any:
        if self._dialect == "postgresql":
            # sqlite3 offre `executemany` sur la connexion ; psycopg ne
            # l'expose que sur le curseur. Le dépôt écrit du sqlite3, donc
            # c'est ici qu'on rétablit l'équivalence.
            cursor = self._raw.cursor()
            cursor.executemany(to_postgres(sql), seq)
            return cursor
        return self._raw.executemany(sql, seq)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self._raw.close()

    def __getattr__(self, name: str) -> Any:
        # Laisse passer ce que le dépôt utilise directement (create_function,
        # cursor, row_factory…) sans avoir à tout redéclarer ici.
        return getattr(self._raw, name)


class Driver(ABC):
    """Ce qu'un moteur doit fournir pour que le dépôt lui parle."""

    dialect: str

    @abstractmethod
    def connect(self) -> Connection: ...

    @property
    @abstractmethod
    def now_sql(self) -> str:
        """Horodatage UTC au format texte 'YYYY-MM-DD HH:MM:SS'.

        Les colonnes de dates sont en TEXT dans le schéma existant. Les deux
        moteurs doivent donc produire exactement la même chaîne, sinon les
        comparaisons de dates divergent silencieusement entre UAT et prod.
        """

    @abstractmethod
    def table_exists(self, conn: Connection, table: str) -> bool: ...

    @abstractmethod
    def table_columns(self, conn: Connection, table: str) -> set[str]: ...

    @abstractmethod
    def nocase(self, expr: str) -> str:
        """Expression de tri insensible à la casse.

        SQLite a `COLLATE NOCASE`, que PostgreSQL ne connaît pas. Le dépôt s'en
        sert 29 fois, surtout pour trier des titres et des noms.
        """

    @abstractmethod
    def insert_or_ignore(self, table: str, columns: Sequence[str]) -> str:
        """INSERT qui ne fait rien si la ligne existe déjà."""

    @abstractmethod
    def fts_filter(self) -> str:
        """Fragment SQL filtrant `videos` sur l'index plein texte, un paramètre."""

    @abstractmethod
    def fts_query(self, raw: str) -> str:
        """Traduit la saisie de l'utilisateur dans la syntaxe du moteur."""

    @abstractmethod
    def fts_upsert(self, conn: "Connection", payload: dict[str, Any]) -> None:
        """Réécrit l'entrée d'index d'une vidéo.

        C'est le seul écart qui ne se traduit pas par une réécriture de chaîne :
        FTS5 range 26 champs dans autant de colonnes, PostgreSQL les fond dans
        un unique `tsvector`. Les deux formes n'ont pas la même arité.
        """


class SQLiteDriver(Driver):
    dialect = "sqlite"

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> Connection:
        raw = sqlite3.connect(self.db_path)
        raw.row_factory = sqlite3.Row
        # WAL : permet au worker d'écrire pendant que l'interface web lit.
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA foreign_keys=ON")
        return Connection(raw, self.dialect)

    @property
    def now_sql(self) -> str:
        return "datetime('now')"

    def table_exists(self, conn: Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,)
        ).fetchone()
        return row is not None

    def table_columns(self, conn: Connection, table: str) -> set[str]:
        return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}

    def nocase(self, expr: str) -> str:
        return f"{expr} COLLATE NOCASE"

    def insert_or_ignore(self, table: str, columns: Sequence[str]) -> str:
        cols = ", ".join(columns)
        marks = ", ".join("?" for _ in columns)
        return f"INSERT OR IGNORE INTO {table}({cols}) VALUES({marks})"

    def fts_filter(self) -> str:
        return "file_id IN (SELECT file_id FROM videos_fts WHERE videos_fts MATCH ?)"

    def fts_query(self, raw: str) -> str:
        # FTS5 : chaque terme préfixé, joints par AND implicite.
        terms = [t for t in re.split(r"[^\w]+", raw or "", flags=re.UNICODE) if t]
        return " ".join(f'"{t}"*' for t in terms)

    def fts_upsert(self, conn: "Connection", payload: dict[str, Any]) -> None:
        file_id = payload.get("file_id", "")
        conn.execute("DELETE FROM videos_fts WHERE file_id = ?", (file_id,))
        cols = ["file_id", *FTS_FIELDS]
        marks = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO videos_fts({', '.join(cols)}) VALUES({marks})",
            tuple(str(payload.get(c, "") or "") for c in cols),
        )


class PostgresDriver(Driver):
    dialect = "postgresql"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def connect(self) -> Connection:
        # Import tardif : la production tourne sur SQLite et ne doit pas avoir
        # besoin de psycopg installé.
        import psycopg

        raw = psycopg.connect(self.dsn, row_factory=_row_factory, autocommit=False)
        return Connection(raw, self.dialect)

    @property
    def now_sql(self) -> str:
        # Même format texte que SQLite, sinon les dates ne se comparent plus.
        return "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"

    def table_exists(self, conn: Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def table_columns(self, conn: Connection, table: str) -> set[str]:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ?",
            (table,),
        ).fetchall()
        return {r[0] for r in rows}

    def nocase(self, expr: str) -> str:
        return f"LOWER({expr})"

    def insert_or_ignore(self, table: str, columns: Sequence[str]) -> str:
        cols = ", ".join(columns)
        marks = ", ".join("?" for _ in columns)
        return f"INSERT INTO {table}({cols}) VALUES({marks}) ON CONFLICT DO NOTHING"

    def fts_filter(self) -> str:
        return (
            "file_id IN (SELECT file_id FROM videos_fts "
            "WHERE document @@ websearch_to_tsquery('french', ?))"
        )

    def fts_query(self, raw: str) -> str:
        # websearch_to_tsquery accepte la langue naturelle telle quelle.
        return (raw or "").strip()

    def fts_upsert(self, conn: "Connection", payload: dict[str, Any]) -> None:
        file_id = payload.get("file_id", "")
        texte = " ".join(str(payload.get(f, "") or "") for f in FTS_FIELDS)
        conn.execute(
            "INSERT INTO videos_fts(file_id, document) "
            "VALUES(?, to_tsvector('french', ?)) "
            "ON CONFLICT (file_id) DO UPDATE SET document = EXCLUDED.document",
            (file_id, texte),
        )


def make_driver(database_url: str = "", db_path: Path | str = "") -> Driver:
    """Choisit le moteur. Sans `DATABASE_URL`, on reste sur SQLite — c'est ce
    que fait la production, et son comportement ne doit pas dépendre d'une
    variable qu'elle ne définit pas."""
    url = (database_url or "").strip()
    if url.startswith(("postgresql://", "postgres://")):
        return PostgresDriver(url)
    if url.startswith("sqlite://"):
        return SQLiteDriver(Path(url.replace("sqlite:///", "").replace("sqlite://", "")))
    if url:
        raise ValueError(f"DATABASE_URL non reconnue : {url!r}")
    return SQLiteDriver(Path(db_path))
