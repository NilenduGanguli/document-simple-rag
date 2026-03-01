"""
Inline ONNX embedding + ChromaDB upsert.

Adapted from services/embedding-service/app/worker.py.
Called directly in the ingestion worker after chunking.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List

import numpy as np
from transformers import BertTokenizerFast

from rag_shared.db.repositories.chunk_repo import ChunkRepository
from rag_shared.db.repositories.document_repo import DocumentRepository
from rag_shared.onnx.math_utils import mean_pooling_np, l2_normalize_np
from rag_shared.metrics import onnx_inference_duration_ms, onnx_pool_wait_ms, embedding_batch_duration_ms

logger = logging.getLogger(__name__)

_BATCH_SIZE = int(os.getenv('EMBEDDING_BATCH_SIZE', '16'))


async def embed_and_store_chunks(
    chunk_ids: List[str],
    doc_id: str,
    app_state,
) -> None:
    """
    Embed a list of chunks using the ONNX bi-encoder, upsert to ChromaDB,
    update chunk/document status in Postgres, then trigger BM25 refresh.

    Parameters
    ----------
    chunk_ids    : list of chunk UUIDs to embed
    doc_id       : parent document ID (for status updates)
    app_state    : FastAPI app.state (contains db_pool, biencoder_pool, etc.)
    """
    session_pool = app_state.biencoder_pool
    tokenizer: BertTokenizerFast = app_state.biencoder_tokenizer
    db_pool = app_state.db_pool
    chroma_collection = app_state.chroma_collection
    bm25_manager = app_state.bm25_manager

    if session_pool is None or tokenizer is None:
        logger.error(f"Doc {doc_id}: biencoder not loaded — cannot embed chunks")
        return

    chunk_repo = ChunkRepository(db_pool)
    doc_repo = DocumentRepository(db_pool)

    # Update doc status to embedding
    await doc_repo.update_status(doc_id, 'embedding')

    loop = asyncio.get_running_loop()

    # Process in batches
    for batch_start in range(0, len(chunk_ids), _BATCH_SIZE):
        batch_chunk_ids = chunk_ids[batch_start:batch_start + _BATCH_SIZE]

        # Fetch chunk texts
        chunks = await chunk_repo.fetch_by_ids(batch_chunk_ids)
        if not chunks:
            logger.warning(f"Doc {doc_id}: no chunks returned for embedding batch")
            continue

        texts = [c['chunk_text'] for c in chunks]
        ids = [c['chunk_id'] for c in chunks]
        parent_ids = [c['parent_document_id'] for c in chunks]

        # Tokenize
        encoded = tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors='np',
        )
        input_ids = encoded['input_ids'].astype(np.int64)
        attention_mask = encoded['attention_mask'].astype(np.int64)
        token_type_ids = encoded.get(
            'token_type_ids', np.zeros_like(input_ids)
        ).astype(np.int64)

        # ONNX inference
        import time as _time
        t0 = _time.monotonic()
        async with session_pool.acquire() as (session, wait_ms):
            onnx_pool_wait_ms.labels(model_type='biencoder').observe(wait_ms)
            outputs = await loop.run_in_executor(
                None,
                lambda: session.run(
                    None,
                    {
                        'input_ids': input_ids,
                        'attention_mask': attention_mask,
                        'token_type_ids': token_type_ids,
                    },
                ),
            )

        infer_ms = (_time.monotonic() - t0) * 1000
        onnx_inference_duration_ms.labels(model_type='biencoder').observe(infer_ms)
        embedding_batch_duration_ms.observe(infer_ms)

        # Mean pool + L2 normalise
        last_hidden = outputs[0]  # [batch, seq_len, 768]
        pooled = mean_pooling_np(last_hidden, encoded['attention_mask'])
        normalised = l2_normalize_np(pooled)  # [batch, 768]

        # Build metadata list for ChromaDB
        metadatas = [
            {"parent_document_id": str(pid)} for pid in parent_ids
        ]

        # Upsert to ChromaDB
        try:
            await chroma_collection.upsert(
                ids=ids,
                embeddings=normalised.tolist(),
                metadatas=metadatas,
            )
        except Exception as exc:
            logger.error(f"ChromaDB upsert failed for doc {doc_id}: {exc}")
            continue

        # Update chunk status to 'done'
        await chunk_repo.bulk_update_status(ids, 'done')

        logger.debug(f"Doc {doc_id}: embedded {len(ids)} chunks in {infer_ms:.1f}ms")

    # Mark document ready if all chunks are embedded
    marked = await doc_repo.mark_ready_if_complete(doc_id)
    if marked:
        logger.info(f"Doc {doc_id}: all chunks embedded — marked ready")

    # Trigger immediate BM25 rebuild
    if bm25_manager is not None:
        await bm25_manager.rebuild_now()
