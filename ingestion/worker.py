"""Le worker : une vidéo, de Google Drive jusqu'aux vecteurs.

Il doit pouvoir tourner des heures sans surveillance, être interrompu à
n'importe quel moment, et reprendre sans refaire ce qui est fait. Trois
principes le gouvernent.

**Rien n'est retraité sans qu'on le demande.** Chaque étape franchie est
inscrite en base ; au redémarrage, on repart de là et non du début. Une vidéo
déjà transcrite ne repasse pas par Whisper, qui est de loin l'étape la plus
coûteuse.

**Une panne passagère n'arrête pas la chaîne.** Un quota Drive dépassé, une
connexion coupée : le travail est remis en file, et seul le plafond de
reprises le déclare perdu.

**Le disque est rendu.** Vidéo et audio temporaires sont supprimés quoi qu'il
arrive, y compris en cas d'échec. Avec des fichiers de trois gigaoctets en
moyenne et douze de libres, un seul oubli suffirait à tout bloquer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from classification.llm_enricher import LLMClient, enrich_with_llm, should_use_llm
from classification.transcript_enricher import enrich_from_transcript
from database.knowledge_repository import KnowledgeRepository
from embeddings.provider import EmbeddingProvider
from ingestion.downloader import NotEnoughSpace, download_to_file
from ingestion.jobs import JobQueue, JobState
from knowledge.chunking import chunk_segments
from transcription.audio import extract_audio, has_audio_track
from transcription.whisper_engine import transcribe
from utils.config import Settings

LOGGER = logging.getLogger(__name__)


@dataclass
class Rapport:
    """Ce qu'une passe du worker a produit. Sert l'observabilité."""

    traites: int = 0
    reussis: int = 0
    echoues: int = 0
    ignores: int = 0
    segments: int = 0
    fragments: int = 0
    vecteurs: int = 0
    secondes_audio: float = 0.0
    termes: int = 0
    sans_metadonnees: int = 0
    duree: float = 0.0
    erreurs: list[str] = field(default_factory=list)

    def resume(self) -> str:
        vitesse = (self.secondes_audio / self.duree) if self.duree else 0.0
        return (
            f"{self.traites} traitée(s) · {self.reussis} réussie(s), "
            f"{self.echoues} échec(s), {self.ignores} ignorée(s) · "
            f"{self.fragments} fragments, {self.vecteurs} vecteurs · "
            f"{vitesse:.2f}× temps réel"
        )


class IngestionWorker:
    def __init__(
        self,
        settings: Settings,
        video_repo: Any,
        knowledge: KnowledgeRepository,
        queue: JobQueue,
        provider: EmbeddingProvider,
        *,
        drive_service: Any = None,
        download: Callable[..., Path] = download_to_file,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.settings = settings
        self.videos = video_repo
        self.knowledge = knowledge
        self.queue = queue
        self.provider = provider
        self.drive_service = drive_service
        self.download = download
        # Sans client, l'enrichissement s'en tient au lexique : c'est le
        # comportement par défaut, gratuit et reproductible.
        self.llm_client = llm_client
        self._whisper = None  # chargé une seule fois, pas à chaque vidéo

    # ── Boucle ──────────────────────────────────────────────────────────────

    def run(self, limite: int | None = None, *, force: bool = False) -> Rapport:
        """Traite les travaux jusqu'à épuisement de la file ou de la limite."""
        rapport = Rapport()
        debut = time.monotonic()

        while limite is None or rapport.traites < limite:
            job = self.queue.claim_next()
            if job is None:
                break
            rapport.traites += 1
            LOGGER.info("[%s] début du traitement", job.file_id)
            try:
                self._traiter(job.file_id, rapport, force=force)
                self.queue.complete(job.file_id)
                rapport.reussis += 1
                LOGGER.info("[%s] terminé", job.file_id)
            except Exception as exc:  # noqa: BLE001 - la chaîne ne doit pas s'arrêter
                message = f"{type(exc).__name__}: {exc}"
                etat = self.queue.fail(job.file_id, message)
                rapport.erreurs.append(f"{job.file_id} — {message}")
                LOGGER.error(
                    "[%s] échec à l'étape %s (tentative %s) : %s",
                    job.file_id, etat.step if etat else "?",
                    etat.retry_count if etat else "?", message,
                )
                if etat and etat.state is JobState.FAILED:
                    rapport.echoues += 1

        rapport.duree = time.monotonic() - debut
        LOGGER.info("Passe terminée — %s", rapport.resume())
        return rapport

    # ── Traitement d'une vidéo ──────────────────────────────────────────────

    def _traiter(self, file_id: str, rapport: Rapport, *, force: bool) -> None:
        video = self.videos.get_video(file_id)
        if video is None:
            raise LookupError(f"vidéo inconnue en base : {file_id}")

        source_uid = video.internal_video_id or file_id

        # Idempotence : la transcription est l'étape chère, on ne la refait pas.
        if not force and self.knowledge.has_transcript(source_uid):
            LOGGER.info("[%s] déjà transcrite, on passe à l'indexation", file_id)
            segments = self.knowledge.get_segments(source_uid)
            self._enrichir(file_id, segments, rapport)
            self._indexer(source_uid, video, segments, rapport)
            rapport.ignores += 1
            return

        temp = Path(self.settings.temp_dir)
        temp.mkdir(parents=True, exist_ok=True)
        chemin_video: Path | None = None
        chemin_audio: Path | None = None

        try:
            # 1) Téléchargement
            self.queue.advance(file_id, JobState.DOWNLOADING, "téléchargement")
            chemin_video = self._telecharger(file_id, video, temp)
            self.queue.advance(file_id, JobState.DOWNLOADED, "téléchargé")

            # 2) Audio
            if not has_audio_track(chemin_video, self.settings.ffmpeg_bin_dir):
                raise ValueError("aucune piste audio")
            self.queue.advance(file_id, JobState.EXTRACTING_AUDIO, "extraction audio")
            chemin_audio = extract_audio(
                chemin_video, temp / f"{source_uid}.wav",
                ffmpeg_bin_dir=self.settings.ffmpeg_bin_dir,
            )

            # La vidéo ne sert plus : on rend les gigaoctets tout de suite,
            # sans attendre la fin de la transcription qui peut durer des heures.
            chemin_video.unlink(missing_ok=True)
            chemin_video = None

            # 3) Transcription
            self.queue.advance(file_id, JobState.TRANSCRIBING, "transcription")
            tr = transcribe(
                chemin_audio,
                model_size=self.settings.whisper_model,
                language=self.settings.whisper_language,
                compute_type=self.settings.whisper_compute_type,
                model=self._modele_whisper(),
            )
            self.knowledge.save_transcript(
                source_uid, tr.segments, model=tr.model,
                languages=tr.languages, duration=tr.duration,
            )
            rapport.segments += len(tr.segments)
            rapport.secondes_audio += tr.duration
            LOGGER.info(
                "[%s] %d segments, langues %s%s",
                file_id, len(tr.segments), tr.languages or "?",
                " (bilingue)" if tr.is_bilingual else "",
            )

            # 4) Métadonnées éditoriales, déduites du texte
            self._enrichir(file_id, tr.segments, rapport)

            # 5) Fragments et vecteurs
            self._indexer(source_uid, video, tr.segments, rapport)

        finally:
            # Le disque est rendu quoi qu'il arrive.
            for chemin in (chemin_video, chemin_audio):
                if chemin is not None:
                    chemin.unlink(missing_ok=True)

    def _enrichir(self, file_id: str, segments: list, rapport: Rapport) -> None:
        """Déduit les métadonnées éditoriales du texte et les range en base.

        Cette étape est délibérément non bloquante : une vidéo transcrite et
        indexée reste utile même si l'étiquetage échoue, alors qu'un worker qui
        s'arrête ici perdrait le travail de transcription déjà payé.
        """
        self.queue.advance(file_id, JobState.GENERATING_METADATA, "métadonnées")
        try:
            enrichissement = enrich_from_transcript(segments)
            origine = "transcript"
            if should_use_llm(enrichissement, self.llm_client):
                LOGGER.info("[%s] lexique muet, second passage par le modèle", file_id)
                enrichissement = enrich_with_llm(segments, self.llm_client)
                origine = "llm"
            if enrichissement.is_empty:
                LOGGER.info("[%s] aucune métadonnée déductible", file_id)
                rapport.sans_metadonnees += 1
                return
            resultat = self.videos.apply_transcript_enrichment(
                file_id, enrichissement, source=origine)
            rapport.termes += resultat["termes"]
            LOGGER.info(
                "[%s] %s / %s — %d termes, colonnes %s",
                file_id, enrichissement.main_theme or "?",
                enrichissement.teaching_type or "?",
                resultat["termes"], resultat["colonnes"] or "déjà remplies",
            )
        except Exception as erreur:                     # noqa: BLE001
            LOGGER.warning("[%s] enrichissement abandonné : %s", file_id, erreur)
            rapport.sans_metadonnees += 1

    def _telecharger(self, file_id: str, video: Any, temp: Path) -> Path:
        if self.drive_service is None:
            raise RuntimeError("aucun accès Drive configuré")
        nom = "".join(c if c.isalnum() or c in "._-" else "_" for c in video.file_name)
        dest = temp / f"{file_id}_{nom}"
        try:
            return self.download(
                self.drive_service, file_id, dest,
                expected_size=int(video.file_size or 0),
            )
        except NotEnoughSpace as exc:
            # Distinguer le manque de place d'une panne réseau : réessayer
            # n'y changera rien tant que le disque n'est pas libéré.
            raise NotEnoughSpace(f"{video.file_name} : {exc}") from exc

    def _indexer(self, source_uid: str, video: Any, segments: list, rapport: Rapport) -> None:
        self.queue.advance(video.file_id, JobState.CHUNKING, "découpage")
        fragments = chunk_segments(
            segments,
            chunk_size=self.settings.chunk_size,
            overlap=self.settings.chunk_overlap,
        )
        self.knowledge.save_chunks(
            source_uid, fragments,
            document_title=video.editorial_title or video.file_name,
            speaker=video.speaker or video.preacher or "",
            source_url=video.drive_url or "",
        )
        rapport.fragments += len(fragments)

        self.queue.advance(video.file_id, JobState.EMBEDDING, "plongements")
        n = 0
        while True:
            lot = self.knowledge.chunks_without_embeddings(limit=200)
            lot = [c for c in lot if c["chunk_uid"].startswith(f"{source_uid}#")]
            if not lot:
                break
            vecteurs = self.provider.embed_passages([c["text"] for c in lot])
            n += self.knowledge.save_embeddings(
                zip([c["id"] for c in lot], vecteurs), model=self.provider.model_name
            )
        rapport.vecteurs += n

        self.queue.advance(video.file_id, JobState.INDEXING, "indexation")
        LOGGER.info("[%s] %d fragments, %d vecteurs", video.file_id, len(fragments), n)

    def _modele_whisper(self):
        """Charge le modèle une fois pour toute la passe.

        Sur une vidéo courte, le chargement domine le temps de transcription ;
        le recharger à chaque travail doublerait la facture.
        """
        if self._whisper is None:
            from transcription.whisper_engine import _load_model

            self._whisper = _load_model(
                self.settings.whisper_model, self.settings.whisper_compute_type
            )
        return self._whisper
