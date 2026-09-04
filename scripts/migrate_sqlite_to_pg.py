"""Recopie la base SQLite dans PostgreSQL. Rejouable autant de fois qu'on veut.

Pendant la phase UAT ce script tourne souvent : on ajuste le schéma, on relance,
on compare. Il doit donc pouvoir être exécuté sans précaution particulière et
laisser la base dans le même état à chaque fois.

    python -m scripts.migrate_sqlite_to_pg --reset
    python -m scripts.migrate_sqlite_to_pg --check
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.driver import make_driver  # noqa: E402
from database.schema import init_database  # noqa: E402
from database.schema_pg import FTS_FIELDS, FTS_SHADOW, ensure_postgres_schema  # noqa: E402
from utils.config import Settings  # noqa: E402
from utils.logging import configure_logging  # noqa: E402

LOGGER = logging.getLogger(__name__)
BATCH = 500


@contextmanager
def migrated_source(path: Path):
    """Rend une copie de la base SQLite, migrations appliquées.

    Le fichier sur disque est en retard sur le code : l'application ajoute ses
    colonnes au démarrage, pas à l'écriture. Au moment de cet audit, le fichier
    portait 68 colonnes et `init_database` en ajoutait 6. Introspecter le
    fichier tel quel produirait donc un schéma PostgreSQL incomplet, et les
    lectures échoueraient sur les colonnes manquantes.

    On travaille sur une copie : le script ne modifie jamais la base source.
    """
    workdir = Path(tempfile.mkdtemp(prefix="ztf-migration-"))
    copie = workdir / path.name
    try:
        shutil.copy(path, copie)
        init_database(copie)
        yield copie
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _sqlite_tables(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            if r[0] not in FTS_SHADOW
        ]
    finally:
        conn.close()


def _copy_table(sqlite_path: Path, driver, table: str, reset: bool) -> int:
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    try:
        cols = [r[1] for r in src.execute(f'PRAGMA table_info("{table}")')]
        if not cols:
            return 0
        quoted = ", ".join(f'"{c}"' for c in cols)
        marks = ", ".join("?" for _ in cols)
        # ON CONFLICT DO NOTHING rend la reprise sûre si le script est
        # interrompu en cours de route.
        insert = f'INSERT INTO "{table}" ({quoted}) VALUES ({marks}) ON CONFLICT DO NOTHING'

        copied = 0
        with driver.connect() as dst:
            cursor = src.execute(f'SELECT {quoted} FROM "{table}"')
            while True:
                rows = cursor.fetchmany(BATCH)
                if not rows:
                    break
                dst.executemany(insert, [tuple(r) for r in rows])
                copied += len(rows)
        return copied
    finally:
        src.close()


def _resync_sequences(driver, tables: list[str]) -> None:
    """Recale les compteurs d'identité après une insertion d'ID explicites.

    Sans ça, le premier INSERT applicatif après migration réutiliserait l'ID 1
    et échouerait sur la contrainte de clé primaire.
    """
    with driver.connect() as conn:
        for table in tables:
            row = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name = ? "
                "AND is_identity = 'YES'",
                (table,),
            ).fetchone()
            if not row:
                continue
            col = row[0]
            conn.execute(
                f'SELECT setval(pg_get_serial_sequence(\'"{table}"\', \'{col}\'), '
                f'COALESCE((SELECT MAX("{col}") FROM "{table}"), 1), true)'
            )


def _rebuild_fts(driver) -> int:
    """Reconstruit l'index plein texte à partir des colonnes de `videos`."""
    fields = ", ".join(f"COALESCE(v.\"{f}\", '')" for f in FTS_FIELDS)
    with driver.connect() as conn:
        conn.execute("TRUNCATE TABLE videos_fts")
        conn.execute(
            f"""
            INSERT INTO videos_fts (file_id, document)
            SELECT v.file_id, to_tsvector('french', concat_ws(' ', {fields}))
            FROM videos v
            """
        )
        return conn.execute("SELECT COUNT(*) FROM videos_fts").fetchone()[0]


def _compare(sqlite_path: Path, driver, tables: list[str]) -> bool:
    """Compare les effectifs table par table. C'est la preuve de la migration."""
    src = sqlite3.connect(sqlite_path)
    ok = True
    try:
        with driver.connect() as dst:
            print(f"  {'table':26} {'sqlite':>8} {'postgres':>9}")
            for table in tables:
                a = src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                b = dst.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                flag = "" if a == b else "  ← ÉCART"
                ok &= a == b
                print(f"  {table:26} {a:8} {b:9}{flag}")
    finally:
        src.close()
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="vide les tables avant de recopier")
    parser.add_argument("--check", action="store_true", help="compare les effectifs sans rien écrire")
    parser.add_argument("--sqlite", default="", help="chemin de la base source")
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level, Path("logs/app.log"))

    sqlite_path = Path(args.sqlite) if args.sqlite else settings.db_path
    if not sqlite_path.exists():
        print(f"  base source introuvable : {sqlite_path}")
        return 1

    database_url = getattr(settings, "database_url", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        print("  DATABASE_URL doit pointer sur PostgreSQL. Rien n'a été fait.")
        return 1

    driver = make_driver(database_url)

    with migrated_source(sqlite_path) as source:
        tables = _sqlite_tables(source)

        if args.check:
            return 0 if _compare(source, driver, tables) else 1

        if args.reset:
            # `CREATE TABLE IF NOT EXISTS` ne modifie pas une table existante :
            # sans cette remise à plat, un schéma déjà créé avec d'anciennes
            # colonnes resterait tel quel et la migration mentirait.
            print("  remise à plat du schéma…")
            with driver.connect() as conn:
                conn.execute("DROP SCHEMA public CASCADE")
                conn.execute("CREATE SCHEMA public")

        print("  création du schéma…")
        made = ensure_postgres_schema(driver, source)
        print(f"    {made['tables']} tables, {made['index']} index")

        print("  copie des données…")
        for table in tables:
            n = _copy_table(source, driver, table, args.reset)
            print(f"    {table:26} {n:8} lignes")

        print("  recalage des compteurs…")
        _resync_sequences(driver, tables)

        print("  reconstruction de l'index plein texte…")
        print(f"    videos_fts                 {_rebuild_fts(driver):8} lignes")

        print("\n  vérification :")
        ok = _compare(source, driver, tables)
    print("\n  RÉSULTAT :", "les deux bases concordent" if ok else "ÉCARTS DÉTECTÉS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
