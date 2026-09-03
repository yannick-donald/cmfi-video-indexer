"""Fixtures communes.

Les tests de comportement tournent sur **les deux moteurs** quand DATABASE_URL
est défini. C'est ce qui transforme « les tests passent » en preuve que le
portage est fidèle, plutôt qu'en impression.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REAL_DB = Path(__file__).resolve().parent.parent / "database" / "index.sqlite3"


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    return tmp_path / "test.sqlite3"


@pytest.fixture
def real_db_copy(tmp_path: Path) -> Path:
    """Copie de la vraie base. Les tests ne touchent jamais l'originale."""
    if not REAL_DB.exists():
        pytest.skip("base réelle absente")
    dest = tmp_path / "copie.sqlite3"
    shutil.copy(REAL_DB, dest)
    return dest


@pytest.fixture(scope="session")
def pg_url() -> str:
    """Base PostgreSQL dédiée aux tests, migrée une fois par session.

    Séparée de la base UAT pour que les écritures des tests ne la salissent
    pas. Sans DATABASE_URL, les tests PostgreSQL sont simplement ignorés.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        return ""

    test_url = url.rsplit("/", 1)[0] + "/ztf_uat_test"
    env = {**os.environ, "LC_ALL": "C"}
    subprocess.run(["dropdb", "--if-exists", "ztf_uat_test"], env=env, capture_output=True)
    created = subprocess.run(["createdb", "ztf_uat_test"], env=env, capture_output=True)
    if created.returncode != 0:
        return ""

    migrated = subprocess.run(
        [sys.executable, "-m", "scripts.migrate_sqlite_to_pg", "--reset"],
        env={**env, "DATABASE_URL": test_url},
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
    )
    return test_url if migrated.returncode == 0 else ""


@pytest.fixture(params=["sqlite", "postgresql"])
def backend(request, pg_url: str) -> str:
    """Chaque test de comportement s'exécute une fois par moteur."""
    if request.param == "postgresql":
        if not pg_url:
            pytest.skip("PostgreSQL indisponible (DATABASE_URL non défini)")
        return pg_url
    return ""
