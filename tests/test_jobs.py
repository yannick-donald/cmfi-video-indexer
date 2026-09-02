"""La file de travaux : idempotence, reprise après interruption, plafond de
reprises. Ce sont les trois propriétés qui permettent au worker de tourner
des heures sans surveillance.
"""

from __future__ import annotations

import pytest

from database.driver import make_driver
from ingestion.jobs import Job, JobQueue, JobState


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
