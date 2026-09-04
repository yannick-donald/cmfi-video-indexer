"""Interface de plongement, et son implémentation locale.

Le code de recherche ne doit jamais parler directement à un modèle : il parle
à `EmbeddingProvider`. Changer de modèle — ou passer un jour à un service
distant — ne doit toucher que ce fichier.

Une distinction compte et se perd facilement : la famille **E5** attend un
préfixe, `query:` pour une question et `passage:` pour un document. L'omettre
ne provoque aucune erreur, seulement des résultats médiocres — le genre de
défaut qu'on ne remarque pas avant longtemps. Les autres familles, dont
`paraphrase-multilingual`, n'en veulent pas : le préfixe y devient du bruit.
Le fournisseur regarde donc le nom du modèle avant d'en ajouter un, et
l'interface garde deux méthodes distinctes pour que l'appelant dise toujours
s'il plonge une question ou un document.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Sequence

LOGGER = logging.getLogger(__name__)

# Choisi pour trois raisons : il est multilingue, il rend des vecteurs de
# dimension 384 — celle du schéma — et il pèse 0,22 Go là où e5-large en
# demande 2,24, ce qui compte sur une machine dont le disque est la contrainte.
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingProvider(ABC):
    """Ce qu'un fournisseur de plongements doit savoir faire."""

    model_name: str
    dimension: int

    @abstractmethod
    def embed_passages(self, textes: Sequence[str]) -> list[list[float]]:
        """Plonge des documents destinés à être retrouvés."""

    @abstractmethod
    def embed_query(self, texte: str) -> list[float]:
        """Plonge une question. Ce n'est pas le même préfixe qu'un document."""


class FastEmbedProvider(EmbeddingProvider):
    """Implémentation locale, par ONNX Runtime.

    `fastembed` est préféré à `sentence-transformers` parce qu'il n'entraîne pas
    PyTorch — près d'un gigaoctet — pour faire tourner un modèle de 120 Mo.
    Sur une machine où le disque est la contrainte qui mord, la différence
    décide.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        dimension: int = 384,
        batch_size: int = 16,
    ) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self.batch_size = batch_size
        # Seule la famille E5 attend des préfixes. Les ajouter ailleurs
        # dégrade la recherche sans rien signaler.
        self.prefixes = "e5" in model_name.lower()
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "fastembed absent. Installez-le : pip install fastembed"
                ) from exc
            LOGGER.info("Chargement du modèle de plongement « %s »", self.model_name)
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed_passages(self, textes: Sequence[str]) -> list[list[float]]:
        propres = [t.strip() for t in textes if t and t.strip()]
        if not propres:
            return []
        if self.prefixes:
            propres = [f"passage: {t}" for t in propres]
        modele = self._load()
        return [list(map(float, v)) for v in modele.embed(propres, batch_size=self.batch_size)]

    def embed_query(self, texte: str) -> list[float]:
        requete = texte.strip()
        if self.prefixes:
            requete = f"query: {requete}"
        vecteurs = list(self._load().embed([requete]))
        return list(map(float, vecteurs[0])) if vecteurs else []


class HashingProvider(EmbeddingProvider):
    """Plongement déterministe sans modèle, pour les tests.

    Il ne porte aucun sens : deux textes proches ne donnent pas des vecteurs
    proches. Il ne sert qu'à exercer le stockage et la mécanique de recherche
    sans charger 120 Mo de modèle à chaque test.
    """

    def __init__(self, dimension: int = 384) -> None:
        self.model_name = "hashing-test"
        self.dimension = dimension

    def _vecteur(self, texte: str) -> list[float]:
        import hashlib
        import math

        graine = hashlib.sha256(texte.encode("utf-8")).digest()
        valeurs = [
            (graine[i % len(graine)] / 255.0) - 0.5 for i in range(self.dimension)
        ]
        norme = math.sqrt(sum(v * v for v in valeurs)) or 1.0
        return [v / norme for v in valeurs]

    def embed_passages(self, textes: Sequence[str]) -> list[list[float]]:
        return [self._vecteur(t) for t in textes if t and t.strip()]

    def embed_query(self, texte: str) -> list[float]:
        return self._vecteur(texte)


def make_provider(model_name: str = "", dimension: int = 384) -> EmbeddingProvider:
    """Choisit le fournisseur. `hashing` réservé aux tests."""
    if model_name in ("hashing", "hashing-test"):
        return HashingProvider(dimension)
    return FastEmbedProvider(model_name or DEFAULT_MODEL, dimension)
