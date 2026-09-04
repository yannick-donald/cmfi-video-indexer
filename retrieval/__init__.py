"""Récupération : recherche vectorielle, lexicale, et leur fusion."""

from retrieval.base import Retriever
from retrieval.dedup import deduplicate
from retrieval.hybrid import HybridRetriever
from retrieval.keyword import KeywordRetriever
from retrieval.vector import VectorRetriever

__all__ = ["Retriever", "VectorRetriever", "KeywordRetriever", "HybridRetriever", "deduplicate"]
