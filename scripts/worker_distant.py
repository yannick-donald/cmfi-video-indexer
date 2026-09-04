#!/usr/bin/env python3
"""Worker de transcription autonome, à lancer sur une machine qui a du CPU.

La base de production est un fichier SQLite sur le disque de Render : aucune
machine extérieure ne peut l'ouvrir. Ce worker ne s'y connecte donc pas — il
parle à l'application en HTTP, par deux routes protégées par un jeton.

Il tourne seul. Personne ne lui assigne de travail : il demande ce qui n'est
pas encore transcrit, le fait, le repose, recommence. Déposer une
transcription suffit à faire sortir une vidéo de la liste, donc l'interrompre
à tout moment ne coûte que la vidéo en cours.

    export WORKER_TOKEN=…
    python scripts/worker_distant.py --url https://cmfi-video-indexer.org
    python scripts/worker_distant.py --asset-type cut --limit 5
    python scripts/worker_distant.py --dry-run        # liste sans rien faire
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.error
import urllib.request
import json

from auth.drive_auth import authenticate
from drive_scanner.client import build_drive_service
from ingestion.downloader import NotEnoughSpace, download_to_file
from transcription.audio import extract_audio, has_audio_track
from transcription.whisper_engine import transcribe
from utils.config import Settings

LOGGER = logging.getLogger("worker")

# En dessous, on ne démarre pas une vidéo : le téléchargement plus l'audio
# extrait dépassent vite plusieurs gigaoctets, et remplir le disque de la
# machine hôte est un dégât qu'on inflige à quelqu'un d'autre.
MARGE_DISQUE_GO = 4.0


class Pont:
    """Le strict nécessaire pour parler à l'application."""

    def __init__(self, base_url: str, token: str, timeout: int = 120) -> None:
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _appel(self, chemin: str, donnees: dict | None = None) -> dict:
        url = f"{self.base}{chemin}"
        corps = json.dumps(donnees).encode() if donnees is not None else None
        requete = urllib.request.Request(
            url,
            data=corps,
            method="POST" if corps else "GET",
            headers={"x-worker-token": self.token,
                     **({"Content-Type": "application/json"} if corps else {})},
        )
        try:
            with urllib.request.urlopen(requete, timeout=self.timeout) as reponse:
                return json.loads(reponse.read().decode())
        except urllib.error.HTTPError as erreur:
            detail = erreur.read().decode()[:200]
            raise RuntimeError(f"HTTP {erreur.code} sur {chemin} — {detail}") from erreur

    def a_faire(self, limite: int, asset_type: str = "") -> list[dict]:
        suffixe = f"&asset_type={asset_type}" if asset_type else ""
        return self._appel(f"/api/worker/pending?limit={limite}{suffixe}")["videos"]

    def deposer(self, file_id: str, transcript) -> dict:
        return self._appel(
            f"/api/worker/{file_id}/transcript",
            {
                "model": transcript.model,
                "languages": transcript.languages,
                "duration": transcript.duration,
                "segments": [
                    {"start": s.start, "end": s.end, "text": s.text, "language": s.language}
                    for s in transcript.segments
                ],
            },
        )


def disque_libre_go(chemin: Path) -> float:
    return shutil.disk_usage(chemin).free / 1e9


def traiter(video: dict, pont: Pont, drive, settings: Settings, langue: str) -> bool:
    file_id = video["file_id"]
    nom = video.get("file_name") or file_id
    taille_go = (video.get("file_size") or 0) / 1e9

    with tempfile.TemporaryDirectory(prefix="cmfi-worker-") as tmp:
        temp = Path(tmp)
        libre = disque_libre_go(temp)
        if libre < taille_go + MARGE_DISQUE_GO:
            LOGGER.warning("%s — %.1f Go libres pour une vidéo de %.1f Go, on passe",
                           nom, libre, taille_go)
            return False

        chemin_video = temp / f"{file_id}.bin"
        chemin_audio = temp / f"{file_id}.wav"
        try:
            LOGGER.info("%s — téléchargement (%.2f Go)", nom, taille_go)
            download_to_file(drive, file_id, chemin_video,
                             expected_size=video.get("file_size") or 0)

            if not has_audio_track(chemin_video, settings.ffmpeg_bin_dir):
                LOGGER.warning("%s — aucune piste audio", nom)
                return False

            extract_audio(chemin_video, chemin_audio,
                          ffmpeg_bin_dir=settings.ffmpeg_bin_dir)
            chemin_video.unlink(missing_ok=True)   # le disque est rendu tôt

            LOGGER.info("%s — transcription", nom)
            debut = time.monotonic()
            resultat = transcribe(
                chemin_audio,
                model_size=settings.whisper_model,
                language=langue,
                compute_type=settings.whisper_compute_type,
            )
            ecoule = time.monotonic() - debut

            reponse = pont.deposer(file_id, resultat)
            enr = reponse.get("enrichment") or {}
            LOGGER.info(
                "%s — %d segments en %.0f min%s",
                nom, reponse["segments"], ecoule / 60,
                f", thème {enr['main_theme']}" if enr.get("main_theme") else "",
            )
            return True

        except NotEnoughSpace as erreur:
            LOGGER.error("%s — disque plein : %s", nom, erreur)
            raise
        except Exception as erreur:                        # noqa: BLE001
            LOGGER.error("%s — abandonnée : %s", nom, erreur)
            return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.getenv("CMFI_URL", "https://cmfi-video-indexer.org"))
    ap.add_argument("--token", default="",
                    help="défaut : WORKER_TOKEN de l'environnement, sinon du .env")
    ap.add_argument("--limit", type=int, default=20, help="vidéos par passage")
    ap.add_argument("--asset-type", default="", help="'cut' pour ne prendre que les découpes")
    ap.add_argument("--language", default="en",
                    help="langue forcée ; 'auto' pour laisser Whisper décider (déconseillé)")
    ap.add_argument("--once", action="store_true", help="un seul passage, puis on s'arrête")
    ap.add_argument("--dry-run", action="store_true", help="affiche la liste sans rien traiter")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")
    settings = Settings()
    # Le jeton ne devrait pas avoir à passer par une ligne de commande, où il
    # finit dans l'historique du shell : le .env du dépôt est déjà l'endroit
    # des secrets, et il est ignoré par git.
    jeton = args.token or os.getenv("WORKER_TOKEN", "") or settings.worker_token
    if not jeton:
        sys.exit("Aucun jeton. Ajoute WORKER_TOKEN=… au .env du dépôt, "
                 "ou exporte-le dans ton shell.")
    pont = Pont(args.url, jeton)

    a_faire = pont.a_faire(args.limit, args.asset_type)
    if args.dry_run:
        print(f"{len(a_faire)} vidéo(s) sans transcription :\n")
        for v in a_faire:
            print(f"  {(v.get('internal_video_id') or v['file_id']):<18}"
                  f" {(v.get('file_size') or 0)/1e9:>6.2f} Go  {v.get('file_name','')[:60]}")
        return 0

    drive = build_drive_service(authenticate())
    faits = rates = 0

    while a_faire:
        for video in a_faire:
            if traiter(video, pont, drive, settings, args.language):
                faits += 1
            else:
                rates += 1
        if args.once:
            break
        a_faire = pont.a_faire(args.limit, args.asset_type)

    LOGGER.info("terminé — %d transcrite(s), %d échec(s)", faits, rates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
