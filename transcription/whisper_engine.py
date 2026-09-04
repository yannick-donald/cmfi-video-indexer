"""Transcription par Whisper, avec détection de langue par fenêtre.

Le corpus est bilingue : un orateur et son interprète alternent. Forcer une
langue est donc à proscrire — Whisper, contraint sur du français alors qu'il
entend de l'anglais, ne produit pas une erreur : il **traduit silencieusement**.
On obtiendrait des citations attribuées à quelqu'un qui n'a jamais prononcé ces
mots, sans qu'aucun signal ne l'indique.

`faster-whisper` est préféré à `openai-whisper` : même modèle, environ quatre
fois plus rapide sur processeur, ce qui compte sur une machine sans carte
graphique.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from knowledge.chunking import Segment

LOGGER = logging.getLogger(__name__)


class WhisperMissing(RuntimeError):
    """faster-whisper n'est pas installé."""


@dataclass(slots=True)
class Transcript:
    segments: list[Segment]
    languages: list[str]
    duration: float
    model: str

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def is_bilingual(self) -> bool:
        return len({lg for lg in self.languages if lg}) > 1


def _load_model(model_size: str, compute_type: str) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise WhisperMissing(
            "faster-whisper absent. Installez-le : pip install faster-whisper"
        ) from exc
    LOGGER.info("Chargement du modèle Whisper « %s » (%s)", model_size, compute_type)
    return WhisperModel(model_size, device="cpu", compute_type=compute_type)


def transcribe(
    audio_path: Path,
    *,
    model_size: str = "base",
    language: str = "auto",
    compute_type: str = "int8",
    on_segment: Callable[[Segment], None] | None = None,
    model: Any | None = None,
    condition_on_previous_text: bool = False,
) -> Transcript:
    """Transcrit un fichier audio en segments horodatés.

    `language="auto"` laisse Whisper détecter. Attention : **sur ce corpus la
    détection est peu fiable** — mesurée le 4 septembre 2026 sur
    CHR-VID-000343, elle a proposé du yoruba à 41 % sur un passage d'anglais
    parfaitement lisible. Ces réunions sont bilingues, un orateur anglophone
    et son interprète francophone ; forcer `language="en"` y donne un texte
    exploitable là où la détection automatique et le français donnent du
    charabia. Sur un corpus de cette nature, une valeur explicite est donc le
    réglage sûr, pas le réglage risqué.

    `condition_on_previous_text=False` par défaut, à l'inverse de
    faster-whisper. Le conditionnement fait boucler le modèle sur un son
    bruité — « Le ponteur est continuement actif » cinq fois de suite. Sans
    lui : deux répétitions sur 844 segments.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    engine = model or _load_model(model_size, compute_type)
    forced = None if language in ("", "auto") else language

    debut = time.monotonic()
    raw_segments, info = engine.transcribe(
        str(audio_path),
        language=forced,
        task="transcribe",          # jamais "translate" : on veut les mots dits
        vad_filter=True,            # coupe les silences, fréquents en convention
        word_timestamps=False,
        beam_size=1,                # sur deux cœurs, un faisceau large ne paie pas
        condition_on_previous_text=condition_on_previous_text,
    )

    segments: list[Segment] = []
    langues: list[str] = []
    for raw in raw_segments:
        # faster-whisper expose la langue par segment quand elle varie ; sinon
        # on retombe sur celle détectée pour l'ensemble.
        lang = getattr(raw, "language", None) or getattr(info, "language", "") or ""
        seg = Segment(
            start=float(raw.start),
            end=float(raw.end),
            text=(raw.text or "").strip(),
            language=lang,
        )
        if not seg.text:
            continue
        segments.append(seg)
        langues.append(lang)
        if on_segment:
            on_segment(seg)

    ecoule = time.monotonic() - debut
    duree = float(getattr(info, "duration", 0.0) or 0.0)
    LOGGER.info(
        "%d segments en %.0f s (%.1f× temps réel), langues : %s",
        len(segments), ecoule, (duree / ecoule) if ecoule else 0.0,
        ", ".join(sorted(set(langues))) or "inconnue",
    )

    return Transcript(
        segments=segments,
        languages=sorted({lg for lg in langues if lg}),
        duration=duree,
        model=model_size,
    )
