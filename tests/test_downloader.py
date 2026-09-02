"""Le téléchargement doit écrire en flux, pas en mémoire, et ne jamais
laisser derrière lui un fichier partiel qui aurait l'air complet.
"""

from __future__ import annotations

import pytest

from ingestion import downloader as dl


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


@pytest.fixture(autouse=True)
def _reset():
    FauxDownloader.instances.clear()


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
