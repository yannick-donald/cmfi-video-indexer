"""Découpage des transcriptions en fragments destinés à la recherche.

Le corpus est bilingue : un orateur et son interprète alternent. Deux règles
en découlent, et elles ne sont pas négociables.

D'abord, **un fragment ne chevauche jamais un changement de langue**. Un
fragment moitié français moitié anglais produit un plongement incohérent : il
ne ressemble à rien et ne remonte jamais en recherche.

Ensuite, **la langue voyage avec le fragment**. Les deux langues sont indexées
à parité ; sans cette étiquette, impossible de dédupliquer un enseignement de
sa traduction au moment de la récupération.

Le fragment ne coupe jamais à l'intérieur d'un segment Whisper : un segment est
une unité de parole, la couper au milieu d'une phrase abîme le sens pour gagner
quelques caractères.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

LOGGER = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 900
DEFAULT_OVERLAP = 150


@dataclass(slots=True)
class Segment:
    """Un segment de transcription, tel que Whisper le rend."""

    start: float
    end: float
    text: str
    language: str = ""
    speaker_role: str = "unknown"
    speaker_label: str = ""

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.0)


@dataclass(slots=True)
class Chunk:
    """Un fragment prêt pour le plongement, avec de quoi remonter à la source."""

    text: str
    start_time: float
    end_time: float
    language: str
    speaker_role: str = "unknown"
    segment_indices: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(self.end_time - self.start_time, 0.0)


def language_runs(segments: list[Segment]) -> Iterator[tuple[int, int, str]]:
    """Découpe la liste en plages de même langue.

    Rend des triplets (début, fin exclue, langue). C'est la frontière que le
    découpage ne franchit jamais.
    """
    if not segments:
        return
    start = 0
    current = segments[0].language or ""
    for i in range(1, len(segments)):
        lang = segments[i].language or ""
        if lang != current:
            yield start, i, current
            start, current = i, lang
    yield start, len(segments), current


def infer_speaker_roles(segments: list[Segment]) -> list[Segment]:
    """Devine qui parle, à partir de l'alternance des langues.

    En interprétation consécutive, l'orateur parle puis l'interprète reprend :
    la première plage d'un couple est donc l'original. On alterne à partir de
    la première plage rencontrée.

    C'est une **heuristique**, et elle se trompe si l'enregistrement s'ouvre
    sur une introduction dans la langue de l'interprète. Elle ne sert qu'à
    orienter l'affichage ; rien de critique n'en dépend, et `speaker_label`
    reste vide jusqu'à ce qu'une vraie diarisation acoustique le remplisse.
    """
    runs = list(language_runs(segments))
    langs = [lang for _, _, lang in runs if lang]
    if len(set(langs)) < 2:
        # Une seule langue : on ne peut rien déduire de l'alternance.
        for seg in segments:
            seg.speaker_role = "unknown"
        return segments

    primaire = langs[0]
    for start, end, lang in runs:
        role = "primary" if lang == primaire else "interpreter"
        for i in range(start, end):
            segments[i].speaker_role = role if lang else "unknown"
    return segments


def _pack(
    segments: list[Segment],
    indices: list[int],
    chunk_size: int,
    overlap: int,
) -> Iterator[Chunk]:
    """Regroupe des segments d'une même langue en fragments."""
    courant: list[int] = []
    longueur = 0

    def rendre(idx: list[int]) -> Chunk:
        pris = [segments[i] for i in idx]
        return Chunk(
            text=" ".join(s.text.strip() for s in pris if s.text.strip()),
            start_time=pris[0].start,
            end_time=pris[-1].end,
            language=pris[0].language,
            speaker_role=pris[0].speaker_role,
            segment_indices=list(idx),
        )

    for i in indices:
        taille = len(segments[i].text)
        if courant and longueur + taille > chunk_size:
            yield rendre(courant)
            # Recouvrement : on repart sur la fin du fragment précédent pour
            # qu'une idée à cheval sur deux fragments reste retrouvable.
            garde: list[int] = []
            reste = 0
            for j in reversed(courant):
                if reste >= overlap:
                    break
                garde.insert(0, j)
                reste += len(segments[j].text)
            courant = garde
            longueur = reste
        courant.append(i)
        longueur += taille

    if courant:
        yield rendre(courant)


def chunk_segments(
    segments: Iterable[Segment],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    infer_roles: bool = True,
) -> list[Chunk]:
    """Transforme des segments de transcription en fragments indexables."""
    segs = [s for s in segments if s.text and s.text.strip()]
    if not segs:
        return []
    if overlap >= chunk_size:
        raise ValueError("CHUNK_OVERLAP doit être inférieur à CHUNK_SIZE")

    if infer_roles:
        infer_speaker_roles(segs)

    chunks: list[Chunk] = []
    for start, end, _lang in language_runs(segs):
        chunks.extend(_pack(segs, list(range(start, end)), chunk_size, overlap))

    LOGGER.info(
        "%d segments -> %d fragments (%s)",
        len(segs), len(chunks), ", ".join(sorted({c.language for c in chunks if c.language})) or "langue inconnue",
    )
    return chunks
