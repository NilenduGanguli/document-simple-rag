"""
Sparse BM25 search — thin wrapper around BM25Manager.search().
"""
from __future__ import annotations

from app.bm25_manager import BM25Manager


def sparse_search(query: str, bm25_manager: BM25Manager, k: int = 100) -> list[dict]:
    """
    Return top-k BM25 results for *query*.

    Args:
        query:        Raw (or preprocessed) query string.
        bm25_manager: Initialised BM25Manager instance.
        k:            Maximum number of results.

    Returns:
        List of dicts: [{'chunk_id': str, 'bm25_score': float}, ...]
    """
    return bm25_manager.search(query, k)
