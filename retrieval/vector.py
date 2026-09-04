"""Recherche par similarité sémantique.

PostgreSQL fait le travail avec pgvector, qui trie côté serveur. SQLite n'a
rien d'équivalent : on relit les vecteurs et on calcule la similarité en NumPy.
Ce n'est pas un pis-aller à l'échelle du corpus — 25 000 vecteurs de dimension
384 tiennent dans 38 Mo et se parcourent en moins de dix millisecondes.
"""

from __future__ import annotations

import logging
import math
from typing import Sequence

from database.driver import Driver
from database.knowledge_repository import ChunkHit, unpack_vector
from embeddings.provider import EmbeddingProvider
from retrieval.base import Retriever

LOGGER = logging.getLogger(__name__)


class VectorRetriever(Retriever):
    def __init__(self, driver: Driver, provider: EmbeddingProvider) -> None:
        self.driver = driver
        self.provider = provider

    def search(self, query: str, top_k: int = 10, *, language: str = "", **_) -> list[ChunkHit]:
        if not query.strip():
            return []
        vecteur = self.provider.embed_query(query)
        if not vecteur:
            return []
        if self.driver.dialect == "postgresql":
            return self._pgvector(vecteur, top_k, language)
        return self._numpy(vecteur, top_k, language)

    def _pgvector(self, vecteur: Sequence[float], top_k: int, language: str) -> list[ChunkHit]:
        litteral = "[" + ",".join(f"{v:.6f}" for v in vecteur) + "]"
        filtre = "AND c.language = ?" if language else ""
        # L'ordre des paramètres suit l'ordre d'apparition : SELECT, filtre,
        # ORDER BY, LIMIT.
        params = [litteral, *([language] if language else []), litteral, top_k]
        with self.driver.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.*, 1 - (e.embedding <=> ?::vector) AS score
                FROM chunk_embeddings e
                JOIN chunks c ON c.id = e.chunk_id
                WHERE 1 = 1 {filtre}
                ORDER BY e.embedding <=> ?::vector
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [ChunkHit.from_row(r, float(r["score"])) for r in rows]

    def _numpy(self, vecteur: Sequence[float], top_k: int, language: str) -> list[ChunkHit]:
        filtre = "AND c.language = ?" if language else ""
        params = (language,) if language else ()
        with self.driver.connect() as conn:
            rows = conn.execute(
                f"SELECT c.*, e.embedding FROM chunk_embeddings e "
                f"JOIN chunks c ON c.id = e.chunk_id WHERE 1 = 1 {filtre}",
                params,
            ).fetchall()
        if not rows:
            return []

        norme_q = math.sqrt(sum(v * v for v in vecteur)) or 1.0
        scores: list[tuple[float, object]] = []
        for r in rows:
            autre = unpack_vector(r["embedding"])
            if len(autre) != len(vecteur):
                continue
            produit = sum(a * b for a, b in zip(vecteur, autre))
            norme = math.sqrt(sum(v * v for v in autre)) or 1.0
            scores.append((produit / (norme_q * norme), r))

        scores.sort(key=lambda t: t[0], reverse=True)
        return [ChunkHit.from_row(r, s) for s, r in scores[:top_k]]
