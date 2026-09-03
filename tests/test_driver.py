"""Le pilote de base : traduction de dialecte et accès aux lignes.

C'est le cœur du portage UAT. Si ces tests passent sur les deux dialectes, le
reste du dépôt peut ignorer sur quel moteur il tourne.
"""

from __future__ import annotations

import pytest

from database.driver import PG_NOW, Row, SQLiteDriver, PostgresDriver, make_driver, to_postgres


class TestChoixDuMoteur:
    def test_url_vide_donne_sqlite(self, tmp_path):
        # La production ne définit pas DATABASE_URL : son comportement ne doit
        # pas dépendre d'une variable qu'elle ignore.
        assert make_driver("", tmp_path / "x.db").dialect == "sqlite"

    def test_url_postgres(self):
        assert make_driver("postgresql://u@h/d").dialect == "postgresql"
        assert make_driver("postgres://u@h/d").dialect == "postgresql"

    def test_url_sqlite_explicite(self):
        assert make_driver("sqlite:///tmp/a.db").dialect == "sqlite"

    def test_url_inconnue_leve(self):
        with pytest.raises(ValueError):
            make_driver("mysql://u@h/d")


class TestMarqueurs:
    def test_remplacement_simple(self):
        assert to_postgres("SELECT * FROM t WHERE id = ?") == "SELECT * FROM t WHERE id = %s"

    def test_point_interrogation_dans_un_litteral_est_preserve(self):
        assert to_postgres("SELECT 'a?b' FROM t WHERE x = ?") == "SELECT 'a?b' FROM t WHERE x = %s"

    def test_apostrophe_echappee(self):
        assert to_postgres("SELECT 'it''s ?' , ?") == "SELECT 'it''s ?' , %s"

    def test_pourcent_litteral_est_double(self):
        # psycopg lirait un % isolé comme un marqueur et échouerait.
        assert to_postgres("SELECT * FROM t WHERE c LIKE '%x%'") == "SELECT * FROM t WHERE c LIKE '%%x%%'"


class TestDialecte:
    def test_horodatage(self):
        assert to_postgres("SELECT datetime('now')") == f"SELECT {PG_NOW}"

    def test_tri_insensible_a_la_casse(self):
        assert to_postgres("ORDER BY file_name COLLATE NOCASE") == "ORDER BY LOWER(file_name)"

    def test_insert_or_ignore(self):
        got = to_postgres("INSERT OR IGNORE INTO labels(name) VALUES(?)")
        assert got == "INSERT INTO labels(name) VALUES(%s) ON CONFLICT DO NOTHING"

    def test_on_conflict_existant_nest_pas_double(self):
        src = "INSERT INTO t(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v = excluded.v"
        assert to_postgres(src).count("ON CONFLICT") == 1

    def test_recherche_plein_texte(self):
        src = "WHERE file_id IN (SELECT file_id FROM videos_fts WHERE videos_fts MATCH ?)"
        assert "websearch_to_tsquery('french', %s)" in to_postgres(src)
        assert "MATCH" not in to_postgres(src)


class TestLignes:
    def test_acces_par_nom_et_par_position(self):
        # 182 lectures par nom et 7 par position dans le dépôt : il faut les deux.
        r = Row(["a", "b"], [1, "x"])
        assert r["a"] == 1 and r[0] == 1
        assert r["b"] == "x" and r[1] == "x"

    def test_reste_un_dict(self):
        r = Row(["a"], [1])
        assert dict(r) == {"a": 1}
        assert "a" in r


class TestEquivalences:
    """Les deux pilotes doivent répondre aux mêmes questions."""

    @pytest.fixture
    def paire(self, tmp_path):
        return SQLiteDriver(tmp_path / "x.db"), PostgresDriver("postgresql://u@h/d")

    def test_nocase(self, paire):
        sq, pg = paire
        assert sq.nocase("t") == "t COLLATE NOCASE"
        assert pg.nocase("t") == "LOWER(t)"

    def test_insert_or_ignore(self, paire):
        sq, pg = paire
        assert sq.insert_or_ignore("t", ["a"]).startswith("INSERT OR IGNORE")
        assert pg.insert_or_ignore("t", ["a"]).endswith("ON CONFLICT DO NOTHING")

    def test_les_deux_produisent_un_filtre_a_un_parametre(self, paire):
        for d in paire:
            assert d.fts_filter().count("?") == 1


class TestEcartsDApi:
    """Différences entre sqlite3 et psycopg que le pilote doit masquer."""

    def test_executemany_existe_sur_la_connexion(self, tmp_path):
        # sqlite3 l'offre sur la connexion, psycopg seulement sur le curseur.
        # Le dépôt écrit du sqlite3 : l'équivalence se rétablit dans le pilote.
        d = make_driver(db_path=tmp_path / "x.db")
        with d.connect() as c:
            c.execute("CREATE TABLE t(a INTEGER, b TEXT)")
            c.executemany("INSERT INTO t(a,b) VALUES(?,?)", [(1, "x"), (2, "y")])
            assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
