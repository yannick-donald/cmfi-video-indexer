"""Recherche lexicale sur les fragments.

Indispensable pour ce que la sémantique retrouve mal : un nom propre, une
référence biblique, une date, un titre exact. « Jean 15:5 » doit sortir
« Jean 15:5 », pas un passage qui parle vaguement du même thème.
"""

from __future__ import annotations

import logging
import re

from database.driver import Driver
from database.knowledge_repository import ChunkHit
from retrieval.base import Retriever

LOGGER = logging.getLogger(__name__)


class KeywordRetriever(Retriever):
    def __init__(self, driver: Driver) -> None:
        self.driver = driver

    def search(self, query: str, top_k: int = 10, *, language: str = "", **_) -> list[ChunkHit]:
        if not query.strip():
            return []
        if self.driver.dialect == "postgresql":
            return self._tsquery(query, top_k, language)
        return self._fts5(query, top_k, language)

    def _tsquery(self, query: str, top_k: int, language: str) -> list[ChunkHit]:
        filtre = "AND c.language = ?" if language else ""
        params = [query, query, *([language] if language else []), top_k]
        with self.driver.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.*, ts_rank(c.document, websearch_to_tsquery('french', ?)) AS score
                FROM chunks c
                WHERE c.document @@ websearch_to_tsquery('french', ?) {filtre}
                ORDER BY score DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [ChunkHit.from_row(r, float(r["score"] or 0.0)) for r in rows]

    def _fts5(self, query: str, top_k: int, language: str) -> list[ChunkHit]:
        termes = [t for t in re.split(r"[^\w]+", query, flags=re.UNICODE) if t]
        if not termes:
            return []
        expression = " ".join(f'"{t}"*' for t in termes)
        filtre = "AND c.language = ?" if language else ""
        with self.driver.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.*, bm25(chunks_fts) AS rang
                FROM chunks_fts
                JOIN chunks c ON c.chunk_uid = chunks_fts.chunk_uid
                WHERE chunks_fts MATCH ? {filtre}
                ORDER BY rang
                LIMIT ?
                """,
                (expression, *((language,) if language else ()), top_k),
            ).fetchall()
        # bm25 rend un score négatif, plus bas = meilleur. On le remet à l'endroit.
        return [ChunkHit.from_row(r, -float(r["rang"] or 0.0)) for r in rows]
