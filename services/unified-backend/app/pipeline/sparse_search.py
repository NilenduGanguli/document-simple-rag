"""
Sparse BM25 search — thin wrapper around BM25Manager.search().
"""
from __future__ import annotations

from app.bm25_manager import BM25Manager


def sparse_search(query: str, bm25_manager: BM25Manager, k: int = 100) -> list[dict]:
    """Return top-k BM25 results for *query*."""
    return bm25_manager.search(query, k)
