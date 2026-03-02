"""
Dense vector search using pgvector cosine similarity.
"""
from __future__ import annotations

import numpy as np

from rag_shared.metrics import dense_search_ms
import time


async def dense_search(
    query_embedding: np.ndarray,
    db_pool,
    k: int = 100,
    filters: dict = None,
) -> list[dict]:
    """
    Return the top-k most similar chunks for *query_embedding* using pgvector.
    """
    t0 = time.monotonic()

    n_results = min(k * 3, 500)
    params: list = [query_embedding.astype(np.float32), n_results]

    filter_clause = ""
    if filters and filters.get('document_ids'):
        doc_ids = filters['document_ids']
        params.append(doc_ids)
        filter_clause = f"AND ce.parent_document_id = ANY(${len(params)}::uuid[])"

    query = f"""
        SELECT c.chunk_id::text,
               c.parent_document_id::text,
               c.chunk_text,
               c.page_number,
               c.chunk_index,
               c.source_type,
               c.chunk_metadata,
               pd.filename,
               (1.0 - (ce.embedding <=> $1))::float AS cosine_score
        FROM chunk_embeddings ce
        JOIN chunks c ON c.chunk_id = ce.chunk_id
        JOIN parent_documents pd ON pd.parent_document_id = c.parent_document_id
        WHERE pd.status = 'ready'
        {filter_clause}
        ORDER BY ce.embedding <=> $1
        LIMIT $2
    """

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    results = [dict(r) for r in rows]

    elapsed_ms = (time.monotonic() - t0) * 1000
    dense_search_ms.observe(elapsed_ms)

    return results
