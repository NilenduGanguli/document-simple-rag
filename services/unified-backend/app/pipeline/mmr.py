"""
Maximum Marginal Relevance (MMR) re-ranking.

MMR selects results that are simultaneously relevant to the query and maximally
diverse with respect to what has already been selected.

Two modes:
  - With embeddings: full cosine-similarity-based MMR (preferred)
  - Without embeddings: score-based MMR using rrf_score/cosine_score + document
    diversity heuristic (fallback when per-candidate embeddings are unavailable)

Reference:
  Carbonell & Goldstein (1998). "The use of MMR, diversity-based reranking for
  reordering documents and producing summaries."
"""
from __future__ import annotations

import numpy as np


def mmr_rerank(
    candidates: list[dict],
    k: int = 20,
    lambda_param: float = 0.7,
    query_embedding: np.ndarray = None,
    candidate_embeddings: list[np.ndarray] = None,
) -> list[dict]:
    """
    Maximum Marginal Relevance re-ranking.

    Args:
        candidates:           Candidate result dicts (must have 'chunk_id').
        k:                    Number of results to select.
        lambda_param:         Trade-off weight: 1.0 = pure relevance,
                              0.0 = pure diversity.
        query_embedding:      L2-normalised query vector (optional).
        candidate_embeddings: Per-candidate embedding vectors aligned with
                              *candidates* (optional).

    Returns:
        Re-ranked list of *k* result dicts.
    """
    if not candidates:
        return []

    k = min(k, len(candidates))

    if candidate_embeddings is not None and query_embedding is not None:
        return _mmr_with_embeddings(
            candidates, candidate_embeddings, query_embedding, k, lambda_param
        )
    return _mmr_score_based(candidates, k, lambda_param)


def _mmr_with_embeddings(
    candidates: list[dict],
    embeddings: list[np.ndarray],
    query_emb: np.ndarray,
    k: int,
    lam: float,
) -> list[dict]:
    """
    Embedding-based MMR.

    Relevance: cosine similarity to the query embedding.
    Redundancy: maximum cosine similarity to any already-selected candidate.
    """
    # Pre-compute cosine similarities between each candidate and the query
    query_sims = np.array([
        _cosine(query_emb, e) for e in embeddings
    ])

    selected_indices: list[int] = []
    remaining: list[int] = list(range(len(candidates)))

    while len(selected_indices) < k and remaining:
        if not selected_indices:
            # First pick: highest relevance
            best = max(remaining, key=lambda i: query_sims[i])
        else:
            selected_embs = [embeddings[i] for i in selected_indices]
            best_score = float('-inf')
            best = remaining[0]

            for i in remaining:
                rel = lam * query_sims[i]
                # Maximum similarity to any already-selected item
                max_sim = max(_cosine(embeddings[i], s_emb) for s_emb in selected_embs)
                div = (1.0 - lam) * max_sim
                mmr_score = rel - div
                if mmr_score > best_score:
                    best_score = mmr_score
                    best = i

        selected_indices.append(best)
        remaining.remove(best)

    return [candidates[i] for i in selected_indices]


def _mmr_score_based(
    candidates: list[dict],
    k: int,
    lam: float,
) -> list[dict]:
    """
    Score-based MMR fallback (no embedding vectors required).

    Relevance: rrf_score (or cosine_score if rrf_score absent).
    Diversity: bonus for selecting from a parent document not yet represented.
    """
    scores = [
        c.get('rrf_score', c.get('cosine_score', 0.0)) for c in candidates
    ]
    selected: list[dict] = []
    remaining: list[int] = list(range(len(candidates)))
    parent_docs_seen: set[str] = set()

    while len(selected) < k and remaining:
        if not selected:
            best_idx = max(remaining, key=lambda i: scores[i])
        else:
            best_score = float('-inf')
            best_idx = remaining[0]

            for i in remaining:
                parent_id = candidates[i].get('parent_document_id', '')
                # Diversity bonus: reward chunks from unseen documents
                diversity_bonus = 0.3 if parent_id not in parent_docs_seen else 0.0
                mmr_score = lam * scores[i] + (1.0 - lam) * diversity_bonus
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = i

        selected.append(candidates[best_idx])
        parent_docs_seen.add(
            candidates[best_idx].get('parent_document_id', '')
        )
        remaining.remove(best_idx)

    return selected


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Safe cosine similarity; returns 0.0 for zero vectors."""
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom < 1e-9:
        return 0.0
    return float(np.dot(a, b) / denom)
