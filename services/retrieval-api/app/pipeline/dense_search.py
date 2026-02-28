"""
Dense vector search using ChromaDB cosine similarity.

Queries the ChromaDB 'chunk_embeddings' collection for approximate nearest
neighbours, then enriches results with chunk text and metadata from PostgreSQL.
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
    chroma_collection=None,
) -> list[dict]:
    """
    Return the top-k most similar chunks for *query_embedding*.

    Args:
        query_embedding: L2-normalised float32 ndarray of shape (768,).
        db_pool:         asyncpg connection pool.
        k:               Maximum number of results to return.
        filters:         Optional filter dict currently supporting
                         'document_ids': list[str].
        chroma_collection: ChromaDB async collection for vector search.

    Returns:
        List of dicts with keys:
          chunk_id, parent_document_id, chunk_text, page_number,
          chunk_index, source_type, chunk_metadata, filename, cosine_score
    """
    if chroma_collection is None:
        return []

    t0 = time.monotonic()

    # Build ChromaDB where filter for document_ids
    where = None
    if filters and filters.get('document_ids'):
        doc_ids = filters['document_ids']
        if len(doc_ids) == 1:
            where = {"parent_document_id": doc_ids[0]}
        else:
            where = {"parent_document_id": {"$in": doc_ids}}

    # 1. Query ChromaDB for ANN candidates (over-fetch to handle status filtering)
    n_results = min(k * 3, 500)
    results = await chroma_collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=n_results,
        where=where,
    )

    chunk_ids = results['ids'][0] if results['ids'] else []
    distances = results['distances'][0] if results.get('distances') else []

    if not chunk_ids:
        elapsed_ms = (time.monotonic() - t0) * 1000
        dense_search_ms.observe(elapsed_ms)
        return []

    # 2. Enrich with PostgreSQL (chunk text, document status filtering)
    async with db_pool.acquire() as conn:
        placeholders = ', '.join(f'${i+1}' for i in range(len(chunk_ids)))
        rows = await conn.fetch(f'''
            SELECT c.chunk_id::text,
                   c.parent_document_id::text,
                   c.chunk_text,
                   c.page_number,
                   c.chunk_index,
                   c.source_type,
                   c.chunk_metadata,
                   pd.filename
            FROM chunks c
            JOIN parent_documents pd ON pd.parent_document_id = c.parent_document_id
            WHERE c.chunk_id::text IN ({placeholders})
              AND pd.status = 'ready'
        ''', *chunk_ids)

    # 3. Merge scores with metadata
    row_map = {r['chunk_id']: dict(r) for r in rows}
    merged = []
    for cid, dist in zip(chunk_ids, distances):
        if cid in row_map:
            entry = row_map[cid]
            entry['cosine_score'] = 1.0 - dist  # ChromaDB cosine distance → similarity
            merged.append(entry)
        if len(merged) >= k:
            break

    elapsed_ms = (time.monotonic() - t0) * 1000
    dense_search_ms.observe(elapsed_ms)

    return merged
