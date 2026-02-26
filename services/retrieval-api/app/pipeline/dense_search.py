"""
Dense HNSW search using pgvector cosine similarity.

Executes a vector nearest-neighbour query against chunk_embeddings using the
pre-built HNSW index.  Sets hnsw.ef_search = 200 per connection for recall
tuning as described in Section 12.2 of the design document.
"""
from __future__ import annotations

import numpy as np

from rag_shared.metrics import pgvector_search_ms
import time


async def dense_search(
    query_embedding: np.ndarray,
    db_pool,
    k: int = 100,
    filters: dict = None,
) -> list[dict]:
    """
    Return the top-k most similar chunks for *query_embedding*.

    Args:
        query_embedding: L2-normalised float32 ndarray of shape (768,).
        db_pool:         asyncpg connection pool.
        k:               Maximum number of results to return.
        filters:         Optional filter dict currently supporting
                         'document_ids': list[str].

    Returns:
        List of dicts with keys:
          chunk_id, parent_document_id, chunk_text, page_number,
          chunk_index, source_type, chunk_metadata, filename, cosine_score
    """
    # Format embedding as pgvector text literal  '[0.1,0.2,...,768th]'
    embedding_str = '[' + ','.join(str(x) for x in query_embedding.tolist()) + ']'

    filter_clause = ""
    filter_params: list = []
    param_idx = 3  # $1 = embedding (used twice), $2 = k; filters start at $3

    if filters:
        if filters.get('document_ids'):
            doc_ids = filters['document_ids']
            placeholders = ', '.join(
                f'${param_idx + i}' for i in range(len(doc_ids))
            )
            filter_clause += f" AND pd.parent_document_id::text IN ({placeholders})"
            filter_params.extend(doc_ids)
            param_idx += len(doc_ids)

        if filters.get('language'):
            filter_clause += f" AND c.language = ${param_idx}"
            filter_params.append(filters['language'])
            param_idx += 1

        if filters.get('source_type'):
            filter_clause += f" AND c.source_type = ${param_idx}"
            filter_params.append(filters['source_type'])
            param_idx += 1

    t0 = time.monotonic()

    async with db_pool.acquire() as conn:
        # ef_search controls recall/speed trade-off of the HNSW graph walk.
        # SET LOCAL applies only to this transaction, not the whole session.
        await conn.execute('SET LOCAL hnsw.ef_search = 200')

        sql = f'''
            SELECT ce.chunk_id::text,
                   ce.parent_document_id::text,
                   c.chunk_text,
                   c.page_number,
                   c.chunk_index,
                   c.source_type,
                   c.chunk_metadata,
                   pd.filename,
                   1 - (ce.embedding <=> $1::vector) AS cosine_score
            FROM chunk_embeddings ce
            JOIN chunks c ON c.chunk_id = ce.chunk_id
            JOIN parent_documents pd
                ON pd.parent_document_id = ce.parent_document_id
            WHERE pd.status = 'ready' {filter_clause}
            ORDER BY ce.embedding <=> $1::vector
            LIMIT $2
        '''

        rows = await conn.fetch(sql, embedding_str, k, *filter_params)

    elapsed_ms = (time.monotonic() - t0) * 1000
    pgvector_search_ms.observe(elapsed_ms)

    return [dict(r) for r in rows]
