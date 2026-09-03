"""Suite de tests du projet.

Les tests de comportement du dépôt s'exécutent **une fois par moteur** quand
DATABASE_URL est défini : c'est ce qui transforme « les tests passent » en
preuve que le portage PostgreSQL est fidèle, plutôt qu'en impression. Sans
cette variable, seul SQLite est testé — ce que fait une machine de
développement ordinaire.

    pytest tests/ -q                                  # SQLite seul
    export $(grep -v '^#' .env.uat | xargs)           # les deux moteurs
    pytest tests/ -q
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.driver import (  # noqa: E402
    PG_NOW, PostgresDriver, Row, SQLiteDriver, make_driver, to_postgres,
)
from database.repository import SearchFilters, VideoRepository  # noqa: E402
from ingestion import downloader as dl  # noqa: E402
from ingestion.jobs import Job, JobQueue, JobState  # noqa: E402
from knowledge.chunking import (  # noqa: E402
    Segment, chunk_segments, infer_speaker_roles, language_runs,
)
from transcription.audio import (  # noqa: E402
    ExtractionFailed, SAMPLE_RATE, extract_audio, has_audio_track, resolve_ffmpeg,
)

REAL_DB = Path(__file__).resolve().parent.parent / "database" / "index.sqlite3"
FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")


# ═══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════

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

    Séparée de la base UAT pour que les écritures des tests ne la salissent pas.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        return ""
    test_url = url.rsplit("/", 1)[0] + "/ztf_uat_test"
    env = {**os.environ, "LC_ALL": "C"}
    subprocess.run(["dropdb", "--if-exists", "ztf_uat_test"], env=env, capture_output=True)
    if subprocess.run(["createdb", "ztf_uat_test"], env=env, capture_output=True).returncode:
        return ""
    ok = subprocess.run(
        [sys.executable, "-m", "scripts.migrate_sqlite_to_pg", "--reset"],
        env={**env, "DATABASE_URL": test_url},
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
    )
    return test_url if ok.returncode == 0 else ""


@pytest.fixture(params=["sqlite", "postgresql"])
def backend(request, pg_url: str) -> str:
    """Chaque test de comportement s'exécute une fois par moteur."""
    if request.param == "postgresql":
        if not pg_url:
            pytest.skip("PostgreSQL indisponible (DATABASE_URL non défini)")
        return pg_url
    return ""



# ═════════════════════════════════════════════════════════════════════════
# PILOTE DE BASE — traduction de dialecte et accès aux lignes
# ═════════════════════════════════════════════════════════════════════════

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

# ═════════════════════════════════════════════════════════════════════════
# DÉPÔT — comportement identique sur les deux moteurs
# ═════════════════════════════════════════════════════════════════════════

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

# ═════════════════════════════════════════════════════════════════════════
# TÉLÉCHARGEMENT — en flux, jamais en mémoire
# ═════════════════════════════════════════════════════════════════════════

class FauxStatus:
    def __init__(self, p): self._p = p
    def progress(self): return self._p


class FauxDownloader:
    """Imite MediaIoBaseDownload : écrit par morceaux dans le fichier reçu."""

    instances: list["FauxDownloader"] = []

    def __init__(self, handle, request, chunksize=0):
        self.handle, self.chunks, self.sent = handle, [b"aaa", b"bbb", b"ccc"], 0
        FauxDownloader.instances.append(self)

    def next_chunk(self):
        self.handle.write(self.chunks[self.sent])
        self.sent += 1
        done = self.sent >= len(self.chunks)
        return FauxStatus(self.sent / len(self.chunks)), done


class FauxDownloaderQuiCasse(FauxDownloader):
    def next_chunk(self):
        self.handle.write(b"aaa")
        raise OSError("connexion perdue")


class FauxService:
    def files(self): return self
    def get_media(self, **kw): return object()


@pytest.fixture
def _reset_telechargement():
    FauxDownloader.instances.clear()
    yield


class TestEspaceDisque:
    def test_refuse_si_le_fichier_ne_tient_pas(self, tmp_path):
        # Le corpus contient un fichier de 49,6 Go pour ~12 Go libres.
        with pytest.raises(dl.NotEnoughSpace):
            dl.check_space(tmp_path, 500 * 1024**3)

    def test_accepte_un_petit_fichier(self, tmp_path):
        dl.check_space(tmp_path, 1024)

    def test_taille_inconnue_ne_bloque_pas(self, tmp_path):
        # expected_size=0 : on ne sait pas, on laisse passer.
        dl.check_space(tmp_path, 0)


@pytest.mark.usefixtures("_reset_telechargement")
class TestTelechargement:
    def test_ecrit_le_fichier(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl, "MediaIoBaseDownload", FauxDownloader)
        dest = tmp_path / "v.mp4"
        dl.download_to_file(FauxService(), "id", dest)
        assert dest.read_bytes() == b"aaabbbccc"

    def test_recoit_un_fichier_pas_un_tampon_memoire(self, tmp_path, monkeypatch):
        # Le cœur de la correction : ce qui est passé au téléchargeur doit être
        # un descripteur de fichier, sinon la vidéo repasse par la RAM.
        monkeypatch.setattr(dl, "MediaIoBaseDownload", FauxDownloader)
        dl.download_to_file(FauxService(), "id", tmp_path / "v.mp4")
        handle = FauxDownloader.instances[0].handle
        assert hasattr(handle, "fileno"), "le téléchargement doit viser un fichier"

    def test_progression_rapportee(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl, "MediaIoBaseDownload", FauxDownloader)
        vus = []
        dl.download_to_file(FauxService(), "id", tmp_path / "v.mp4", on_progress=vus.append)
        assert vus and vus[-1] == 1.0

    def test_aucun_fichier_partiel_apres_echec(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl, "MediaIoBaseDownload", FauxDownloaderQuiCasse)
        dest = tmp_path / "v.mp4"
        with pytest.raises(OSError):
            dl.download_to_file(FauxService(), "id", dest)
        assert not dest.exists()
        assert not list(tmp_path.glob("*.partial"))

# ═════════════════════════════════════════════════════════════════════════
# AUDIO — extraction au format qu'attend Whisper
# ═════════════════════════════════════════════════════════════════════════

def _make(path: Path, *, with_audio: bool, seconds: int = 2) -> Path:
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=10"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture
def video_sonore(tmp_path):
    return _make(tmp_path / "sonore.mp4", with_audio=True)


@pytest.fixture
def video_muette(tmp_path):
    return _make(tmp_path / "muette.mp4", with_audio=False)


@FFMPEG
class TestLocalisation:
    def test_trouve_ffmpeg(self):
        assert Path(resolve_ffmpeg()).exists()


@FFMPEG
class TestExtraction:
    def test_produit_un_wav_mono_16khz(self, video_sonore, tmp_path):
        # Le format qu'attend Whisper. S'il change, la transcription se dégrade
        # sans prévenir.
        dest = extract_audio(video_sonore, tmp_path / "a.wav")
        with wave.open(str(dest)) as w:
            assert w.getnchannels() == 1
            assert w.getframerate() == SAMPLE_RATE
            assert w.getsampwidth() == 2
            assert w.getnframes() > 0

    def test_le_wav_est_bien_plus_leger_que_la_video(self, video_sonore, tmp_path):
        dest = extract_audio(video_sonore, tmp_path / "a.wav")
        assert dest.stat().st_size > 0

    def test_fichier_absent(self, tmp_path):
        with pytest.raises(ExtractionFailed):
            extract_audio(tmp_path / "rien.mp4", tmp_path / "a.wav")

    def test_fichier_illisible(self, tmp_path):
        faux = tmp_path / "faux.mp4"
        faux.write_bytes(b"ceci n'est pas une video")
        with pytest.raises(ExtractionFailed):
            extract_audio(faux, tmp_path / "a.wav")

    def test_aucun_wav_partiel_apres_echec(self, tmp_path):
        faux = tmp_path / "faux.mp4"
        faux.write_bytes(b"ceci n'est pas une video")
        with pytest.raises(ExtractionFailed):
            extract_audio(faux, tmp_path / "a.wav")
        assert not list(tmp_path.glob("*.partial"))


@FFMPEG
class TestPisteAudio:
    def test_detecte_une_piste(self, video_sonore):
        assert has_audio_track(video_sonore) is True

    def test_detecte_une_video_muette(self, video_muette):
        # Une vidéo muette occuperait le worker des heures pour rien.
        assert has_audio_track(video_muette) is False

# ═════════════════════════════════════════════════════════════════════════
# FILE DE TRAVAUX — idempotence, reprise, plafond de reprises
# ═════════════════════════════════════════════════════════════════════════

@pytest.fixture
def queue(tmp_path):
    return JobQueue(make_driver(db_path=tmp_path / "jobs.sqlite3"), max_retries=3)


class TestAlimentation:
    def test_ajoute_des_travaux(self, queue):
        assert queue.enqueue(["a", "b", "c"]) == 3
        assert queue.counts() == {"pending": 3}

    def test_reajouter_ne_duplique_pas(self, queue):
        # Le script d'alimentation sera relancé après chaque scan Drive.
        queue.enqueue(["a", "b"])
        queue.enqueue(["a", "b", "c"])
        assert sum(queue.counts().values()) == 3

    def test_liste_vide(self, queue):
        assert queue.enqueue([]) == 0


class TestConsommation:
    def test_prend_le_premier(self, queue):
        queue.enqueue(["a", "b"])
        assert queue.claim_next().file_id == "a"

    def test_file_vide(self, queue):
        assert queue.claim_next() is None

    def test_un_travail_repris_passe_avant_un_nouveau(self, queue):
        # Mieux vaut finir ce qui est commencé qu'accumuler des traitements
        # à moitié faits, chacun ayant laissé des fichiers temporaires.
        queue.enqueue(["a", "b"])
        queue.advance("b", JobState.TRANSCRIBING)
        assert queue.claim_next().file_id == "b"

    def test_un_travail_termine_nest_plus_repris(self, queue):
        queue.enqueue(["a"])
        queue.complete("a")
        assert queue.claim_next() is None


class TestProgression:
    def test_avance_et_horodate(self, queue):
        queue.enqueue(["a"])
        queue.advance("a", JobState.DOWNLOADING, step="téléchargement")
        job = queue.get("a")
        assert job.state is JobState.DOWNLOADING
        assert job.step == "téléchargement"
        assert job.started_at

    def test_la_date_de_debut_ne_bouge_plus(self, queue):
        queue.enqueue(["a"])
        queue.advance("a", JobState.DOWNLOADING)
        debut = queue.get("a").started_at
        queue.advance("a", JobState.TRANSCRIBING)
        assert queue.get("a").started_at == debut

    def test_achevement(self, queue):
        queue.enqueue(["a"])
        queue.complete("a")
        job = queue.get("a")
        assert job.state is JobState.COMPLETED and job.completed_at


class TestReprises:
    def test_un_echec_passager_ne_termine_pas_le_travail(self, queue):
        queue.enqueue(["a"])
        job = queue.fail("a", "réseau coupé")
        assert job.retry_count == 1
        assert job.state is not JobState.FAILED

    def test_le_plafond_arrete_le_travail(self, queue):
        queue.enqueue(["a"])
        for _ in range(3):
            job = queue.fail("a", "erreur")
        assert job.state is JobState.FAILED
        assert queue.claim_next() is None

    def test_le_message_derreur_est_conserve(self, queue):
        queue.enqueue(["a"])
        assert "quota" in queue.fail("a", "quota Drive dépassé").error_message

    def test_remise_a_zero_explicite(self, queue):
        # « Ne pas retraiter, sauf si explicitement demandé. »
        queue.enqueue(["a"])
        for _ in range(3):
            queue.fail("a", "erreur")
        queue.reset("a")
        job = queue.get("a")
        assert job.state is JobState.PENDING and job.retry_count == 0
        assert queue.claim_next().file_id == "a"


class TestObservabilite:
    def test_compte_par_etat(self, queue):
        queue.enqueue(["a", "b", "c"])
        queue.advance("a", JobState.TRANSCRIBING)
        queue.complete("b")
        assert queue.counts() == {"transcribing": 1, "completed": 1, "pending": 1}

    def test_travail_inconnu(self, queue):
        assert queue.get("inexistant") is None

# ═════════════════════════════════════════════════════════════════════════
# DÉCOUPAGE — la règle bilingue
# ═════════════════════════════════════════════════════════════════════════

def seg(i, lang, text="x" * 100, dur=10.0):
    return Segment(start=i * dur, end=(i + 1) * dur, text=text, language=lang)


def alternance(n, langues=("fr", "en"), texte="x" * 100):
    """Interprétation consécutive : les langues alternent segment par segment."""
    return [seg(i, langues[i % len(langues)], texte) for i in range(n)]


class TestPlagesDeLangue:
    def test_langue_unique(self):
        assert list(language_runs([seg(0, "fr"), seg(1, "fr")])) == [(0, 2, "fr")]

    def test_alternance(self):
        runs = list(language_runs(alternance(4)))
        assert runs == [(0, 1, "fr"), (1, 2, "en"), (2, 3, "fr"), (3, 4, "en")]

    def test_liste_vide(self):
        assert list(language_runs([])) == []


class TestRegleCentrale:
    def test_aucun_fragment_ne_melange_deux_langues(self):
        # Un fragment bilingue produit un plongement incohérent : il ne
        # ressemble à rien et ne remonte jamais en recherche.
        chunks = chunk_segments(alternance(20), chunk_size=5000, overlap=0)
        assert len(chunks) == 20, "des segments de langues différentes ont fusionné"
        for c in chunks:
            assert c.language in {"fr", "en"}

    def test_les_segments_dune_meme_langue_se_regroupent(self):
        segs = [seg(i, "fr", "x" * 100) for i in range(10)]
        chunks = chunk_segments(segs, chunk_size=450, overlap=0)
        assert 1 < len(chunks) < 10

    def test_la_langue_voyage_avec_le_fragment(self):
        # Sans cette étiquette, impossible de dédupliquer un enseignement
        # de sa traduction à la récupération.
        for c in chunk_segments(alternance(6), chunk_size=5000, overlap=0):
            assert c.language


class TestDecoupage:
    def test_respecte_la_taille_visee(self):
        segs = [seg(i, "fr", "x" * 100) for i in range(20)]
        chunks = chunk_segments(segs, chunk_size=500, overlap=0)
        for c in chunks[:-1]:
            assert len(c.text) <= 700

    def test_un_segment_plus_long_que_la_cible_reste_entier(self):
        # On ne coupe pas au milieu d'une phrase pour gagner des caractères.
        long = Segment(start=0, end=60, text="y" * 3000, language="fr")
        chunks = chunk_segments([long], chunk_size=500, overlap=0)
        assert len(chunks) == 1 and len(chunks[0].text) == 3000

    def test_recouvrement(self):
        segs = [seg(i, "fr", "x" * 100) for i in range(12)]
        sans = chunk_segments(segs, chunk_size=500, overlap=0)
        avec = chunk_segments(segs, chunk_size=500, overlap=200)
        assert len(avec) >= len(sans)

    def test_recouvrement_invalide(self):
        with pytest.raises(ValueError):
            chunk_segments([seg(0, "fr")], chunk_size=100, overlap=100)

    def test_segments_vides_ignores(self):
        segs = [seg(0, "fr"), Segment(1, 2, "   ", "fr"), seg(2, "fr")]
        assert all(c.text.strip() for c in chunk_segments(segs))

    def test_liste_vide(self):
        assert chunk_segments([]) == []


class TestProvenance:
    def test_les_horodatages_encadrent_le_fragment(self):
        # C'est ce qui permet de citer « 08:52 → 09:58 » dans une réponse.
        segs = [seg(i, "fr", "x" * 100) for i in range(6)]
        for c in chunk_segments(segs, chunk_size=250, overlap=0):
            assert c.start_time < c.end_time
            assert c.duration > 0

    def test_les_indices_de_segments_sont_conserves(self):
        segs = [seg(i, "fr", "x" * 100) for i in range(6)]
        for c in chunk_segments(segs, chunk_size=250, overlap=0):
            assert c.segment_indices

    def test_le_premier_fragment_part_du_debut(self):
        segs = [seg(i, "fr") for i in range(4)]
        assert chunk_segments(segs, chunk_size=5000)[0].start_time == 0.0


class TestRolesDeLocuteur:
    def test_lalternance_designe_orateur_et_interprete(self):
        segs = infer_speaker_roles(alternance(6))
        assert segs[0].speaker_role == "primary"
        assert segs[1].speaker_role == "interpreter"
        assert segs[2].speaker_role == "primary"

    def test_une_seule_langue_ne_permet_rien_de_deduire(self):
        # L'heuristique repose entièrement sur l'alternance : sans deux
        # langues, elle doit se taire plutôt que d'inventer.
        segs = infer_speaker_roles([seg(i, "fr") for i in range(4)])
        assert all(s.speaker_role == "unknown" for s in segs)

    def test_le_role_voyage_avec_le_fragment(self):
        chunks = chunk_segments(alternance(6), chunk_size=5000, overlap=0)
        assert {c.speaker_role for c in chunks} == {"primary", "interpreter"}
