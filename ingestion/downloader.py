"""Téléchargement Drive en flux, sans jamais tenir le fichier en mémoire.

`MediaIoBaseDownload` écrit dans n'importe quel objet fichier. L'ancien code
lui donnait un `BytesIO`, ce qui bufferisait la vidéo entière en RAM avant de
l'écrire sur disque : 3 Go pour un fichier moyen du corpus, 49,6 Go pour le
plus gros. On lui donne un descripteur de fichier ouvert, et la mémoire
consommée retombe à la taille d'un morceau.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable

from googleapiclient.http import MediaIoBaseDownload

from utils.retry import execute_with_retry

LOGGER = logging.getLogger(__name__)

CHUNK_SIZE = 8 * 1024 * 1024

# Marge laissée libre sur le disque après le téléchargement. En dessous, macOS
# devient instable et le reste du pipeline (audio, modèles) n'a plus de place.
SAFETY_MARGIN_BYTES = 2 * 1024 * 1024 * 1024


class NotEnoughSpace(RuntimeError):
    """Le fichier ne tient pas sur le disque, marge de sécurité comprise."""


def free_space(path: Path) -> int:
    return shutil.disk_usage(path).free


def check_space(dest_dir: Path, needed_bytes: int) -> None:
    """Refuse un téléchargement qui remplirait le disque.

    Le corpus contient un fichier de 49,6 Go. Sans ce garde-fou, le worker
    remplirait le disque et échouerait à mi-parcours, en laissant derrière lui
    un fichier partiel de plusieurs dizaines de gigaoctets.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    available = free_space(dest_dir)
    if needed_bytes and available < needed_bytes + SAFETY_MARGIN_BYTES:
        raise NotEnoughSpace(
            f"{needed_bytes / 1024**3:.1f} Go demandés, "
            f"{available / 1024**3:.1f} Go libres "
            f"(marge de {SAFETY_MARGIN_BYTES / 1024**3:.0f} Go exigée)"
        )


def download_to_file(
    service: Any,
    file_id: str,
    dest: Path,
    *,
    expected_size: int = 0,
    chunk_size: int = CHUNK_SIZE,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Télécharge un fichier Drive vers `dest`, morceau par morceau.

    Le fichier est écrit sous un nom temporaire puis renommé : une interruption
    ne laisse donc jamais un fichier partiel qui aurait l'air complet.
    """
    dest = Path(dest)
    check_space(dest.parent, expected_size)

    partial = dest.with_suffix(dest.suffix + ".partial")
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

    try:
        with partial.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request, chunksize=chunk_size)
            done = False
            while not done:
                status, done = execute_with_retry(lambda: downloader.next_chunk())
                if status and on_progress:
                    on_progress(status.progress())
        partial.replace(dest)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    LOGGER.info("Téléchargé %s (%.1f Mo)", dest.name, dest.stat().st_size / 1024**2)
    return dest
