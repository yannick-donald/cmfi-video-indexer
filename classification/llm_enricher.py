"""Second passage : un modèle de langue, seulement là où le lexique a échoué.

Le lexique de `transcript_enricher` ne conclut que sur ce qu'il peut prouver.
Sur une transcription très abîmée, ou sur un message dont le vocabulaire sort
du référentiel, il ne rend rien. C'est le seul cas que ce module traite.

Trois garde-fous, parce qu'un LLM produit du texte plausible même quand il se
trompe :

- **Il ne passe qu'en second.** `should_use_llm()` est la seule porte d'entrée,
  et elle exige que le lexique soit revenu vide. Un terme prouvé par une
  citation vaut mieux qu'un terme deviné.
- **Ses résultats restent distinguables.** Ils sont écrits avec
  `source="llm"` et une confiance plafonnée, jamais mélangés aux termes
  déduits du texte. Un opérateur peut les filtrer ou les révoquer en bloc.
- **Il ne choisit pas son vocabulaire.** Le modèle doit se prononcer dans les
  catégories déjà en base, pas inventer les siennes — sinon le référentiel se
  disperse et la recherche par facettes cesse de fonctionner.

Aucun fournisseur n'est câblé ici : `LLMClient` est un protocole à une méthode.
Branche ce que tu veux (Anthropic, un modèle local via Ollama, autre) du moment
qu'il sait rendre du texte à partir d'une invite.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol, runtime_checkable

from classification.transcript_enricher import (
    THEMES,
    TEACHING_TYPES,
    Terme,
    TranscriptEnrichment,
    _normaliser,
)

LOGGER = logging.getLogger(__name__)

MOTS_MAX = 3000          # ce qu'on envoie au modèle, au plus
CONFIANCE_MAX = 0.60     # un terme deviné ne dépasse jamais un terme prouvé


@runtime_checkable
class LLMClient(Protocol):
    """Le strict minimum attendu d'un fournisseur."""

    def complete(self, prompt: str) -> str:
        """Rend la réponse du modèle, en texte."""
        ...


def should_use_llm(base: TranscriptEnrichment, client: LLMClient | None) -> bool:
    """Le LLM ne passe que si un client est configuré et le lexique bredouille."""
    return client is not None and base.is_empty


def _echantillonner(segments, mots_max: int = MOTS_MAX) -> str:
    """Réduit la transcription en gardant sa répartition dans la durée.

    Tronquer au début donnerait l'ouverture et la louange, jamais la
    prédication. On prélève donc à pas régulier sur toute la longueur.
    """
    segments = [_normaliser(s) for s in segments]
    segments = [s for s in segments if s["text"].strip()]
    total = sum(len(s["text"].split()) for s in segments)
    if total <= mots_max:
        retenus = segments
    else:
        pas = total / mots_max
        retenus, dette = [], 0.0
        for seg in segments:
            dette += len(seg["text"].split())
            if dette >= pas:
                retenus.append(seg)
                dette = 0.0
    return "\n".join(
        f"[{int(s['start'] // 60):03d}min] {' '.join(s['text'].split())}" for s in retenus
    )


def construire_invite(segments) -> str:
    themes = ", ".join(sorted(THEMES))
    types = ", ".join(sorted(TEACHING_TYPES))
    return f"""Voici la transcription automatique, partielle et bruitée, d'une réunion chrétienne filmée. Les noms propres y sont souvent déformés.

Réponds uniquement par un objet JSON, sans commentaire, de la forme :
{{"main_theme": "", "teaching_type": "", "bible_references": [], "biblical_topics": [], "summary": ""}}

Contraintes :
- "main_theme" doit valoir exactement l'une de ces valeurs : {themes}
- "teaching_type" doit valoir exactement l'une de ces valeurs : {types}
- "bible_references" : références au format "John 5" ou "John 5:8", uniquement celles dont le texte porte trace.
- "biblical_topics" : épisodes ou notions bibliques identifiables (ex. "Pool of Bethesda").
- "summary" : deux phrases en français, factuelles.
- Laisse un champ vide plutôt que de deviner. Un champ vide est une réponse acceptable.

TRANSCRIPTION :
{_echantillonner(segments)}"""


def _extraire_json(reponse: str) -> dict:
    """Récupère l'objet JSON même si le modèle l'a entouré de texte."""
    bloc = re.search(r"\{.*\}", reponse, re.S)
    if not bloc:
        raise ValueError("aucun objet JSON dans la réponse du modèle")
    return json.loads(bloc.group(0))


def enrich_with_llm(segments, client: LLMClient) -> TranscriptEnrichment:
    """Interroge le modèle et rend le même type que le lexique.

    Toute réponse hors vocabulaire est écartée : le modèle propose, le
    référentiel dispose.
    """
    donnees = _extraire_json(client.complete(construire_invite(segments)))

    theme = str(donnees.get("main_theme") or "").strip()
    type_ens = str(donnees.get("teaching_type") or "").strip()
    if theme and theme not in THEMES:
        LOGGER.info("thème hors référentiel écarté : %r", theme)
        theme = ""
    if type_ens and type_ens not in TEACHING_TYPES:
        LOGGER.info("type hors référentiel écarté : %r", type_ens)
        type_ens = ""

    refs = [str(r).strip() for r in donnees.get("bible_references") or [] if str(r).strip()]
    sujets = [str(t).strip() for t in donnees.get("biblical_topics") or [] if str(t).strip()]
    resume = str(donnees.get("summary") or "").strip()

    preuve = "déduit par modèle de langue, sans citation à l'appui"
    termes: list[Terme] = []
    if theme:
        termes.append(Terme("theme", theme, CONFIANCE_MAX, 0, 0, preuve))
    if type_ens:
        termes.append(Terme("teaching_type", type_ens, CONFIANCE_MAX, 0, 0, preuve))
    termes += [Terme("scripture", r, CONFIANCE_MAX - 0.1, 0, 0, preuve) for r in refs]
    termes += [Terme("topic", t, CONFIANCE_MAX - 0.1, 0, 0, preuve) for t in sujets]

    return TranscriptEnrichment(
        main_theme=theme,
        teaching_type=type_ens,
        spiritual_themes=[theme] if theme else [],
        bible_references=refs,
        biblical_topics=sujets,
        keywords=([theme] if theme else []) + sujets[:3],
        terms=termes,
        word_count=sum(len(_normaliser(s)["text"].split()) for s in segments),
    )
