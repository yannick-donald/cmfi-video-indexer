"""L'interface que toute recherche respecte.

Le RAG, l'API et un éventuel agent ne parlent qu'à `Retriever`. Ils ignorent
si la réponse vient d'un index vectoriel, d'un index lexical ou de la fusion
des deux — ce qui permet de changer la stratégie sans toucher à ce qui l'utilise.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from database.knowledge_repository import ChunkHit


class Retriever(ABC):
    @abstractmethod
    def search(self, query: str, top_k: int = 10, **filtres) -> list[ChunkHit]:
        """Rend les fragments les plus pertinents, du meilleur au moins bon."""
