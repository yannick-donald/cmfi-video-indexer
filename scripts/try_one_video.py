"""Essai du pipeline sur une seule vidéo locale, sans toucher à la base.

    python -m scripts.try_one_video /chemin/vers/video.mp4

Enchaîne extraction audio, transcription et découpage, puis affiche ce qui
sortirait. Rien n'est écrit en base : c'est un banc d'essai.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge.chunking import chunk_segments  # noqa: E402
from transcription.audio import extract_audio, has_audio_track  # noqa: E402
from transcription.whisper_engine import transcribe  # noqa: E402
from utils.config import Settings  # noqa: E402


def mmss(s: float) -> str:
    return f"{int(s) // 60:02d}:{int(s) % 60:02d}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("video", help="fichier vidéo ou audio local")
    p.add_argument("--model", default="", help="taille du modèle Whisper")
    p.add_argument("--language", default="", help="langue forcée (déconseillé)")
    p.add_argument("--keep-audio", action="store_true", help="ne pas supprimer le WAV")
    p.add_argument("--segments", type=int, default=12, help="segments à afficher")
    args = p.parse_args()

    s = Settings()
    source = Path(args.video).expanduser()
    if not source.exists():
        print(f"  fichier introuvable : {source}")
        return 1

    modele = args.model or s.whisper_model
    langue = args.language or s.whisper_language

    print(f"\n  source   {source.name}  ({source.stat().st_size / 1024**2:.0f} Mo)")

    if not has_audio_track(source, s.ffmpeg_bin_dir):
        print("  ARRÊT : aucune piste audio dans ce fichier.")
        return 1

    tmp = Path(s.temp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    wav = tmp / (source.stem + ".wav")

    print("\n  ── extraction audio ──")
    t0 = time.monotonic()
    extract_audio(source, wav, ffmpeg_bin_dir=s.ffmpeg_bin_dir)
    print(f"  {wav.name}  {wav.stat().st_size / 1024**2:.1f} Mo  en {time.monotonic() - t0:.1f} s")

    print(f"\n  ── transcription (modèle « {modele} », langue « {langue} ») ──")
    t0 = time.monotonic()
    tr = transcribe(wav, model_size=modele, language=langue,
                    compute_type=s.whisper_compute_type)
    ecoule = time.monotonic() - t0
    vitesse = (tr.duration / ecoule) if ecoule else 0
    print(f"  {len(tr.segments)} segments · {tr.duration:.0f} s d'audio "
          f"en {ecoule:.0f} s ({vitesse:.1f}× temps réel)")
    print(f"  langues détectées : {', '.join(tr.languages) or 'aucune'}"
          f"{'   ← BILINGUE' if tr.is_bilingual else ''}")

    if tr.segments:
        print(f"\n  ── {min(args.segments, len(tr.segments))} premiers segments ──")
        for seg in tr.segments[:args.segments]:
            print(f"  [{mmss(seg.start)}→{mmss(seg.end)}] {seg.language or '??'}  {seg.text[:78]}")

    chunks = chunk_segments(tr.segments, chunk_size=s.chunk_size, overlap=s.chunk_overlap)
    print(f"\n  ── découpage ──")
    print(f"  {len(chunks)} fragments")
    for c in chunks[:3]:
        print(f"  [{mmss(c.start_time)}→{mmss(c.end_time)}] {c.language} "
              f"{c.speaker_role:12} {len(c.text):4} car.  {c.text[:60]}…")
    if len({c.language for c in chunks}) > 1:
        print("  aucun fragment ne chevauche un changement de langue.")

    if args.keep_audio:
        print(f"\n  audio conservé : {wav}")
    else:
        wav.unlink(missing_ok=True)
        print("\n  audio temporaire supprimé.")

    if tr.duration and vitesse:
        heures = 600
        print(f"\n  à cette vitesse, {heures} h de corpus demanderaient "
              f"≈ {heures / vitesse:.0f} h de calcul.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
