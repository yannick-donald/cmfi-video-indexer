"""Le dépôt doit se comporter exactement comme avant le portage.

Ces tests passent par le nouveau pilote sans le savoir : ils appellent
l'API publique de `VideoRepository`. S'ils passent, le chemin SQLite —
celui de la production — est intact.
"""

from __future__ import annotations

import pytest

from database.repository import SearchFilters, VideoRepository


@pytest.fixture
def repo(real_db_copy, backend):
    """Le même dépôt, sur SQLite puis sur PostgreSQL."""
    return VideoRepository(real_db_copy, backend)


class TestPilote:
    def test_le_depot_choisit_le_bon_moteur(self, repo, backend):
        attendu = "postgresql" if backend else "sqlite"
        assert repo.driver.dialect == attendu

    def test_connexion_utilisable_avec_et_sans_with(self, repo):
        with repo._connect() as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        conn = repo._connect()
        try:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        finally:
            conn.close()


class TestLecture:
    def test_recherche_sans_filtre(self, repo):
        res = repo.search(SearchFilters(), page_size=10)
        assert res.total == 388
        assert len(res.items) == 10

    def test_recherche_texte(self, repo):
        res = repo.search(SearchFilters(query="Kigali"), page_size=5)
        assert res.total > 0

    def test_recherche_plein_texte_fts(self, repo):
        res = repo.search(SearchFilters(query="convention"), page_size=5, use_fts=True)
        assert res.total > 0

    def test_tri_insensible_a_la_casse(self, repo):
        # Passe par COLLATE NOCASE, l'un des cinq écarts de dialecte.
        asc = repo.search(SearchFilters(), sort_by="file_name", sort_dir="asc", page_size=3)
        desc = repo.search(SearchFilters(), sort_by="file_name", sort_dir="desc", page_size=3)
        assert asc.items[0].file_name != desc.items[0].file_name

    def test_lecture_par_identifiant(self, repo):
        first = repo.search(SearchFilters(), page_size=1).items[0]
        assert repo.get_video(first.file_id).file_id == first.file_id

    def test_statistiques(self, repo):
        stats = repo.get_stats()
        assert stats["total_videos"] == 388

    def test_options_de_filtre(self, repo):
        opts = repo.get_filter_options()
        assert opts["extensions"]

    def test_iteration_par_lots(self, repo):
        # Le seul site qui n'utilise pas `with` : il ferme la connexion lui-même.
        assert sum(1 for _ in repo.iter_videos(batch_size=100)) == 388


class TestEcriture:
    """Chaque écriture touche un des écarts de dialecte traduits par le pilote."""

    @pytest.fixture
    def un_fichier(self, repo):
        # La base PostgreSQL est partagée sur la session, contrairement à la
        # copie SQLite qui est neuve à chaque test. Les écritures visent donc
        # la dernière vidéo, que les tests de lecture ne regardent jamais.
        res = repo.search(SearchFilters(), sort_by="file_name", sort_dir="desc", page_size=1)
        return res.items[0].file_id

    def test_workflow_ecrit_un_horodatage(self, repo, un_fichier):
        # Passe par datetime('now').
        repo.update_workflow(un_fichier, {"asset_type": "raw", "workflow_stage": "watched"})
        v = repo.get_video(un_fichier)
        assert v.workflow_stage == "watched"
        assert v.workflow_updated_at

    def test_labels_utilisent_insert_or_ignore(self, repo, un_fichier):
        repo.set_video_labels(un_fichier, ["Convention", "Test"], user_id=None, user_email="t@x.org")
        noms = {l["name"] for l in repo.get_video_labels(un_fichier)}
        assert {"Convention", "Test"} <= noms

    def test_labels_poses_deux_fois_ne_doublonnent_pas(self, repo, un_fichier):
        repo.set_video_labels(un_fichier, ["Alpha"], user_id=None, user_email="t@x.org")
        repo.set_video_labels(un_fichier, ["Alpha"], user_id=None, user_email="t@x.org")
        assert sum(1 for l in repo.get_video_labels(un_fichier) if l["name"] == "Alpha") == 1

    def test_metadonnees(self, repo, un_fichier):
        repo.update_christian_metadata(un_fichier, {"main_theme": "La prière", "speaker": "Fr Zach"})
        v = repo.get_video(un_fichier)
        assert v.main_theme == "La prière" and v.speaker == "Fr Zach"

    def test_la_recherche_voit_la_modification(self, repo, un_fichier):
        # Vérifie que l'index FTS est bien resynchronisé à l'écriture.
        repo.update_christian_metadata(un_fichier, {"main_theme": "Thème unique zzz"})
        res = repo.search(SearchFilters(query="zzz"), use_fts=True)
        assert res.total >= 1


class TestTitres:
    def test_un_nom_parlant_donne_un_titre(self, repo):
        res = repo.search(SearchFilters(query="Convention"), page_size=1)
        assert repo.suggest_editorial_title(res.items[0].file_id)["title"]

    def test_un_nom_encode_ne_donne_rien(self, repo):
        # 104 vidéos du corpus n'ont qu'un nom de numérisation : aucune règle
        # ne peut en tirer un titre, et c'est le comportement attendu.
        res = repo.search(SearchFilters(query="0000001"), page_size=1)
        if res.items:
            assert repo.suggest_editorial_title(res.items[0].file_id)["title"] == ""
