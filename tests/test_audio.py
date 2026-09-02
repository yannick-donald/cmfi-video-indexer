"""Extraction audio. Les vidéos de test sont fabriquées par ffmpeg lui-même :
aucun fichier binaire dans le dépôt, aucun téléchargement pendant les tests.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from transcription.audio import (
    ExtractionFailed,
    SAMPLE_RATE,
    extract_audio,
    has_audio_track,
    resolve_ffmpeg,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")


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


class TestLocalisation:
    def test_trouve_ffmpeg(self):
        assert Path(resolve_ffmpeg()).exists()


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


class TestPisteAudio:
    def test_detecte_une_piste(self, video_sonore):
        assert has_audio_track(video_sonore) is True

    def test_detecte_une_video_muette(self, video_muette):
        # Une vidéo muette occuperait le worker des heures pour rien.
        assert has_audio_track(video_muette) is False
