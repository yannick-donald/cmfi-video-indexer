"""Fixtures communes. Chaque test travaille sur une base jetable."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REAL_DB = Path(__file__).resolve().parent.parent / "database" / "index.sqlite3"


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """Base neuve, créée par les migrations du projet."""
    return tmp_path / "test.sqlite3"


@pytest.fixture
def real_db_copy(tmp_path: Path) -> Path:
    """Copie de la vraie base. Les tests ne touchent jamais l'originale."""
    if not REAL_DB.exists():
        pytest.skip("base réelle absente")
    dest = tmp_path / "copie.sqlite3"
    shutil.copy(REAL_DB, dest)
    return dest
