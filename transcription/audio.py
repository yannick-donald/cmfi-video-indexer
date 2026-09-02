"""Extraction de la piste audio, au format qu'attend Whisper.

Whisper travaille en WAV mono 16 kHz. Extraire ce format plutôt que de laisser
le modèle rééchantillonner divise la taille par cent environ — une heure de
vidéo pèse 1 à 3 Go, la même heure en WAV 16 kHz mono pèse 115 Mo — ce qui
compte quand il reste douze gigaoctets de disque.

FFmpeg n'est pas réimplémenté : on l'appelle, comme le fait déjà
`metadata/ffprobe_extractor.py`, et on reprend sa façon de localiser le binaire.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
CHANNELS = 1


class FfmpegMissing(RuntimeError):
    """ffmpeg est introuvable."""


class ExtractionFailed(RuntimeError):
    """ffmpeg a refusé le fichier."""


def resolve_ffmpeg(bin_dir: str = "") -> str:
    """Localise ffmpeg : d'abord FFMPEG_BIN_DIR, puis le PATH.

    Même ordre que `FfprobeExtractor._resolve_ffprobe`, pour que les deux
    outils se configurent de la même façon.
    """
    if bin_dir:
        candidate = Path(bin_dir) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if candidate.exists():
            return str(candidate)
    found = shutil.which("ffmpeg")
    if not found:
        raise FfmpegMissing(
            "ffmpeg introuvable. Installez FFmpeg ou renseignez FFMPEG_BIN_DIR dans .env"
        )
    return found


def extract_audio(
    source: Path,
    dest: Path,
    *,
    ffmpeg_bin_dir: str = "",
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    timeout: int = 3600,
) -> Path:
    """Écrit la piste audio de `source` dans `dest`, en WAV mono 16 kHz.

    Comme pour le téléchargement, on écrit sous un nom temporaire puis on
    renomme : une extraction interrompue ne laisse pas un WAV tronqué qui
    passerait ensuite pour valide.
    """
    source, dest = Path(source), Path(dest)
    if not source.exists():
        raise ExtractionFailed(f"fichier source absent : {source}")

    ffmpeg = resolve_ffmpeg(ffmpeg_bin_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")

    cmd = [
        ffmpeg,
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-vn",                     # pas de vidéo
        "-ac", str(channels),
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        "-f", "wav",
        str(partial),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        partial.unlink(missing_ok=True)
        raise ExtractionFailed(f"ffmpeg a dépassé {timeout} s sur {source.name}") from exc

    if result.returncode != 0 or not partial.exists():
        detail = (result.stderr or "").strip().splitlines()
        partial.unlink(missing_ok=True)
        raise ExtractionFailed(
            f"ffmpeg a échoué sur {source.name} : {detail[-1] if detail else 'raison inconnue'}"
        )

    partial.replace(dest)
    LOGGER.info(
        "Audio extrait : %s (%.1f Mo)", dest.name, dest.stat().st_size / 1024**2
    )
    return dest


def has_audio_track(source: Path, ffmpeg_bin_dir: str = "") -> bool:
    """Dit si le fichier contient une piste audio.

    Une vidéo muette ne doit pas partir en transcription : elle occuperait le
    worker pendant des heures pour ne rien produire.
    """
    ffprobe = shutil.which("ffprobe") or ""
    if ffmpeg_bin_dir:
        candidate = Path(ffmpeg_bin_dir) / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if candidate.exists():
            ffprobe = str(candidate)
    if not ffprobe:
        return True  # dans le doute, on laisse ffmpeg trancher

    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(source)],
        capture_output=True, text=True,
    )
    return "audio" in (result.stdout or "")
