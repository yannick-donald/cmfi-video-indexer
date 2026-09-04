"""Fusion des deux recherches.

La sémantique retrouve ce qui se ressemble ; le lexique retrouve ce qui
s'écrit pareil. Aucune des deux ne suffit : « Que dit-il de la marche avec
Dieu ? » appelle la première, « Jean 15:5 » appelle la seconde.

La fusion se fait par rang réciproque (RRF). C'est délibérément un classement
par **position** et non par score : les scores des deux moteurs ne sont pas
comparables — une similarité cosinus vit entre 0 et 1, un ts_rank n'a pas de
borne — et vouloir les normaliser revient à inventer une échelle commune.
"""

from __future__ import annotations

import logging

from database.knowledge_repository import ChunkHit
from retrieval.base import Retriever

LOGGER = logging.getLogger(__name__)

# Constante d'amortissement du RRF. 60 est la valeur de la publication
# d'origine ; elle empêche les toutes premières places d'écraser le reste.
K = 60


class HybridRetriever(Retriever):
    def __init__(
        self,
        vector: Retriever,
        keyword: Retriever,
        *,
        poids_vecteur: float = 1.0,
        poids_lexique: float = 1.0,
    ) -> None:
        self.vector = vector
        self.keyword = keyword
        self.poids_vecteur = poids_vecteur
        self.poids_lexique = poids_lexique

    def search(self, query: str, top_k: int = 10, **filtres) -> list[ChunkHit]:
        if not query.strip():
            return []
        # On puise plus large que demandé : la fusion doit avoir de quoi
        # rapprocher deux listes qui ne se recouvrent pas forcément.
        large = max(top_k * 3, 30)
        listes = [
            (self.vector.search(query, large, **filtres), self.poids_vecteur),
            (self.keyword.search(query, large, **filtres), self.poids_lexique),
        ]

        scores: dict[str, float] = {}
        vus: dict[str, ChunkHit] = {}
        for hits, poids in listes:
            for rang, hit in enumerate(hits, start=1):
                scores[hit.chunk_uid] = scores.get(hit.chunk_uid, 0.0) + poids / (K + rang)
                vus.setdefault(hit.chunk_uid, hit)

        classes = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        sortie: list[ChunkHit] = []
        for uid, score in classes[:top_k]:
            hit = vus[uid]
            hit.score = score
            sortie.append(hit)
        return sortie
