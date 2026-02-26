"""
Reciprocal Rank Fusion (RRF) — merges dense and sparse result lists.

RRF score formula: score(d) = Σ  1 / (k_rrf + rank_i(d))

where the sum is over all ranking lists that contain document d.

This implementation merges two lists (dense + sparse) but is trivially
generalisable to N lists by extending the loop.
"""
from __future__ import annotations


def reciprocal_rank_fusion(
    dense_results: list[dict],
    sparse_results: list[dict],
    k_rrf: int = 60,
) -> list[dict]:
    """
    Fuse dense (cosine) and sparse (BM25) result lists.

    Args:
        dense_results:  Ordered list of dicts from dense_search().
                        Each dict must have a 'chunk_id' key.
        sparse_results: Ordered list of dicts from sparse_search().
                        Each dict must have 'chunk_id' and 'bm25_score' keys.
        k_rrf:          RRF smoothing hyper-parameter (default 60, per original
                        Cormack et al. 2009 paper recommendation).

    Returns:
        Merged list sorted by rrf_score (descending).  Each entry contains
        the original dense dict fields (if present) plus:
          - bm25_score  (float)
          - rrf_score   (float)
    """
    rrf_scores: dict[str, float] = {}
    result_map: dict[str, dict] = {}

    # --- Dense ranking contribution ---
    for rank, result in enumerate(dense_results, start=1):
        cid = result['chunk_id']
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k_rrf + rank)
        # Initialise from dense result; bm25_score defaults to 0
        if cid not in result_map:
            result_map[cid] = dict(result)
            result_map[cid].setdefault('bm25_score', 0.0)

    # --- Sparse ranking contribution ---
    for rank, result in enumerate(sparse_results, start=1):
        cid = result['chunk_id']
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k_rrf + rank)
        if cid not in result_map:
            # Appeared only in sparse; dense fields will be missing but
            # the caller should handle that (e.g. fetch from DB if needed).
            result_map[cid] = dict(result)
        # Always propagate the BM25 score from sparse results
        result_map[cid]['bm25_score'] = result.get('bm25_score', 0.0)

    # --- Sort by RRF score and annotate ---
    merged: list[dict] = []
    for cid, score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True):
        entry = result_map[cid].copy()
        entry['rrf_score'] = score
        merged.append(entry)

    return merged
