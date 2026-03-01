"""
Retrieval router — POST /retrieve, POST /retrieve/batch, GET /retrieve/audit/{id}

Full hybrid pipeline:
  1.  Redis result cache check
  2.  NER query preprocessing (normalise + entity extraction)
  3.  Bi-encoder query embedding (ONNX INT8)
  4.  Dense vector search (ChromaDB)
  5.  Sparse BM25 search (in-memory BM25Okapi)
  6.  Reciprocal Rank Fusion (RRF)
  7.  MMR re-ranking (diversity)
  8.  Cross-encoder re-ranking (top-N candidates)
  9.  Result aggregation  (k_chunks or n_documents)
  10. Retrieval audit insert (background task)
  11. Redis result cache set (TTL 5 min)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from typing import List, Optional

import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from rag_shared.auth.api_key import hash_api_key
from rag_shared.config import get_settings
from rag_shared.metrics import retrieval_latency_ms

from app.schemas import (
    ChunkResult,
    DocumentResult,
    RetrievalConfig,
    RetrievalRequest,
    RetrievalResponse,
)
from app.pipeline.dense_search import dense_search
from app.pipeline.sparse_search import sparse_search
from app.pipeline.rrf import reciprocal_rank_fusion
from app.pipeline.mmr import mmr_rerank

# ──────────────────────────────────────────────────────────────────────────────
# API key dependency
# ──────────────────────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(
    api_key: str = Security(_api_key_header),
) -> str:
    """FastAPI Security dependency — validates X-API-Key header."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required",
        )
    valid_keys = get_settings().get_api_keys_list()
    if api_key in valid_keys:
        return hash_api_key(api_key)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid API key",
    )


logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/retrieve", tags=["retrieval"])

# Result cache TTL (seconds)
_RESULT_CACHE_TTL = 300


# ──────────────────────────────────────────────────────────────────────────────
# Dependency helpers — pull shared state from app.state
# ──────────────────────────────────────────────────────────────────────────────

def _get_app_state(request: Request):
    return request.app.state


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _cache_key(req: RetrievalRequest) -> str:
    payload = req.model_dump_json(exclude_none=True)
    digest = hashlib.md5(payload.encode()).hexdigest()
    return f"retrieval:{digest}"


async def _embed_query(query: str, app_state) -> np.ndarray:
    """
    Embed a single query string using the bi-encoder ONNX session pool.
    Returns a float32 L2-normalised embedding of shape (768,).
    """
    from rag_shared.onnx.math_utils import mean_pooling_np, l2_normalize_np
    from rag_shared.metrics import onnx_inference_duration_ms, onnx_pool_wait_ms

    loop = asyncio.get_running_loop()
    tokenizer = app_state.biencoder_tokenizer
    session_pool = app_state.biencoder_pool

    encoded = tokenizer(
        [query],
        padding=True,
        truncation=True,
        max_length=int(os.getenv("MAX_QUERY_TOKENS", "100")),
        return_tensors='np',
    )
    input_ids = encoded['input_ids'].astype(np.int64)
    attention_mask = encoded['attention_mask'].astype(np.int64)
    token_type_ids = encoded.get(
        'token_type_ids', np.zeros_like(input_ids)
    ).astype(np.int64)

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

    last_hidden = outputs[0]  # [1, seq_len, 768]
    pooled = mean_pooling_np(last_hidden, encoded['attention_mask'])
    normalised = l2_normalize_np(pooled)  # [1, 768]
    return normalised[0]


def _aggregate_n_documents(
    chunks: list[dict],
    n: int,
    filename_map: dict,
) -> list[DocumentResult]:
    """
    Group chunk results by parent_document_id and return the top-n documents.

    The document score is the score of its highest-ranked chunk.
    """
    doc_buckets: dict[str, list[dict]] = {}
    for chunk in chunks:
        pid = chunk.get('parent_document_id', '')
        doc_buckets.setdefault(pid, []).append(chunk)

    # Sort documents by best chunk score
    doc_list = sorted(
        doc_buckets.items(),
        key=lambda kv: kv[1][0].get('rerank_score') or kv[1][0].get('rrf_score', 0),
        reverse=True,
    )[:n]

    results: list[DocumentResult] = []
    for pid, chunks_for_doc in doc_list:
        primary = _to_chunk_result(chunks_for_doc[0])
        supporting = [_to_chunk_result(c) for c in chunks_for_doc[1:]]
        doc_score = primary.rerank_score or primary.rrf_score or primary.cosine_score
        results.append(
            DocumentResult(
                parent_document_id=pid,
                filename=filename_map.get(pid, ''),
                primary_chunk=primary,
                supporting_chunks=supporting,
                document_score=doc_score,
            )
        )
    return results


def _to_chunk_result(d: dict) -> ChunkResult:
    """Convert a raw result dict to a ChunkResult, with safe defaults."""
    return ChunkResult(
        chunk_id=d.get('chunk_id', ''),
        parent_document_id=d.get('parent_document_id', ''),
        chunk_text=d.get('chunk_text', ''),
        page_number=d.get('page_number'),
        chunk_index=d.get('chunk_index', 0),
        source_type=d.get('source_type', 'text'),
        cosine_score=float(d.get('cosine_score', 0.0)),
        bm25_score=float(d.get('bm25_score', 0.0)),
        rrf_score=float(d.get('rrf_score', 0.0)),
        rerank_score=float(d['rerank_score']) if d.get('rerank_score') is not None else None,
    )


async def _log_audit(
    db_pool,
    audit_id: str,
    request: RetrievalRequest,
    processed_query: str,
    entities: list[str],
    query_embedding: np.ndarray,
    dense_results: list[dict],
    sparse_results: list[dict],
    rrf_results: list[dict],
    mmr_results: list[dict],
    final_results: list[dict],
    latency_total_ms: float,
    client_ip: str,
    api_key_hash: str,
) -> None:
    """Insert a retrieval audit record into the retrieval_audit table."""
    # Store embedding as JSON text
    embedding_str = json.dumps(query_embedding.tolist())

    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO retrieval_audit (
                    audit_id, query_raw, query_processed, entities_detected,
                    query_embedding,
                    retrieval_mode, k_requested, n_requested,
                    dense_candidates, sparse_candidates,
                    rrf_scores, mmr_selected, final_ranked,
                    latency_ms, client_ip, api_key_hash
                ) VALUES (
                    $1::uuid, $2, $3, $4::jsonb,
                    $5,
                    $6, $7, $8,
                    $9::jsonb, $10::jsonb,
                    $11::jsonb, $12::jsonb, $13::jsonb,
                    $14, $15::inet, $16
                )
                """,
                audit_id,
                request.query,
                processed_query,
                json.dumps(entities),
                embedding_str,
                request.mode,
                request.k,
                request.n,
                json.dumps([r.get('chunk_id') for r in dense_results[:20]]),
                json.dumps([r.get('chunk_id') for r in sparse_results[:20]]),
                json.dumps([
                    {'chunk_id': r.get('chunk_id'), 'rrf_score': r.get('rrf_score')}
                    for r in rrf_results[:20]
                ]),
                json.dumps([r.get('chunk_id') for r in mmr_results[:20]]),
                json.dumps([r.get('chunk_id') for r in final_results]),
                int(latency_total_ms),
                client_ip or '0.0.0.0',
                api_key_hash,
            )
    except Exception as exc:
        logger.error(f"Audit log insert failed (non-fatal): {exc}", exc_info=True)


# ──────────────────────────────────────────────────────────────────────────────
# POST /retrieve  — single hybrid retrieval
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=RetrievalResponse,
    summary="Hybrid RAG retrieval (dense + sparse + rerank + MMR)",
)
async def retrieve(
    request_body: RetrievalRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    api_key_hash: str = Depends(_require_api_key),
) -> RetrievalResponse:
    app_state = http_request.app.state
    redis = app_state.redis
    db_pool = app_state.db_pool
    bm25_mgr = app_state.bm25_manager
    config = request_body.config or RetrievalConfig()

    t_total = time.monotonic()
    latency: dict[str, float] = {}
    audit_id = str(uuid.uuid4())

    # ── 1. Redis result cache ─────────────────────────────────────────────────
    cache_key = _cache_key(request_body)
    cached_raw = await redis.get(cache_key)
    if cached_raw:
        logger.debug(f"Result cache hit: key={cache_key}")
        return RetrievalResponse.model_validate_json(cached_raw)

    # ── 2. NER query preprocessing ────────────────────────────────────────────
    t0 = time.monotonic()
    preprocessor = app_state.query_preprocessor
    if config.enable_ner and preprocessor is not None:
        processed_query, entities = await preprocessor.process(request_body.query)
    else:
        from app.pipeline.query_preprocessor import _normalise
        processed_query = _normalise(request_body.query)
        entities = []
    latency['ner_ms'] = (time.monotonic() - t0) * 1000

    # ── 2b. Stopword removal (per-search-type) ───────────────────────────────
    if config.enable_stopword_removal_dense or config.enable_stopword_removal_sparse:
        from app.pipeline.query_preprocessor import _remove_stopwords
    dense_query = _remove_stopwords(processed_query) if config.enable_stopword_removal_dense else processed_query
    sparse_query = _remove_stopwords(processed_query) if config.enable_stopword_removal_sparse else processed_query

    # ── 3. Embed query ────────────────────────────────────────────────────────
    t0 = time.monotonic()
    query_embedding = await _embed_query(dense_query, app_state)
    latency['embedding_ms'] = (time.monotonic() - t0) * 1000

    # ── 4. Dense vector search (ChromaDB) ───────────────────────────────────
    t0 = time.monotonic()
    filters_dict = (
        request_body.filters.model_dump(exclude_none=True)
        if request_body.filters
        else None
    )
    dense_results = await dense_search(
        query_embedding=query_embedding,
        db_pool=db_pool,
        k=config.dense_candidates,
        filters=filters_dict,
        chroma_collection=getattr(app_state, 'chroma_collection', None),
    )
    latency['dense_search_ms'] = (time.monotonic() - t0) * 1000
    logger.debug(f"Dense search: {len(dense_results)} results")

    # ── 5. Sparse BM25 search ─────────────────────────────────────────────────
    t0 = time.monotonic()
    sparse_results = sparse_search(
        query=sparse_query,
        bm25_manager=bm25_mgr,
        k=config.sparse_candidates,
    )
    latency['sparse_search_ms'] = (time.monotonic() - t0) * 1000
    logger.debug(f"Sparse search: {len(sparse_results)} results")

    # ── 6. RRF fusion ─────────────────────────────────────────────────────────
    t0 = time.monotonic()
    rrf_results = reciprocal_rank_fusion(
        dense_results,
        sparse_results,
        k_rrf_dense=config.k_rrf_dense,
        k_rrf_sparse=config.k_rrf_sparse,
    )
    latency['rrf_ms'] = (time.monotonic() - t0) * 1000

    # ── 7. MMR re-ranking ─────────────────────────────────────────────────────
    t0 = time.monotonic()
    mmr_candidates = rrf_results[: config.rerank_candidates]
    mmr_results = mmr_rerank(
        candidates=mmr_candidates,
        k=config.rerank_candidates,
        lambda_param=config.mmr_lambda,
    )
    latency['mmr_ms'] = (time.monotonic() - t0) * 1000

    # ── 8. Cross-encoder re-ranking ───────────────────────────────────────────
    reranker = getattr(app_state, 'reranker', None)
    if config.enable_reranking and reranker is not None:
        t0 = time.monotonic()
        mmr_results = await reranker.rerank(
            query=dense_query,
            candidates=mmr_results[: config.rerank_candidates],
        )
        latency['rerank_ms'] = (time.monotonic() - t0) * 1000
    else:
        latency['rerank_ms'] = 0.0

    # ── 9. Result aggregation ─────────────────────────────────────────────────
    if request_body.mode == 'k_chunks':
        k = request_body.k or 10
        final_chunks_raw = mmr_results[:k]
        chunk_results = [_to_chunk_result(r) for r in final_chunks_raw]
        response = RetrievalResponse(
            query=request_body.query,
            mode=request_body.mode,
            audit_id=audit_id,
            results_k_chunks=chunk_results,
            total_results=len(chunk_results),
            latency_breakdown=latency,
            entities_detected=entities,
        )
    else:  # n_documents
        n = request_body.n or 5
        # Build filename map from dense results (they include 'filename')
        filename_map = {
            r['parent_document_id']: r.get('filename', '')
            for r in dense_results
            if r.get('parent_document_id')
        }
        doc_results = _aggregate_n_documents(mmr_results, n, filename_map)
        response = RetrievalResponse(
            query=request_body.query,
            mode=request_body.mode,
            audit_id=audit_id,
            results_n_documents=doc_results,
            total_results=len(doc_results),
            latency_breakdown=latency,
            entities_detected=entities,
        )

    # ── 10. Audit log (fire-and-forget background task) ───────────────────────
    client_ip = (
        http_request.client.host if http_request.client else None
    )
    total_ms = (time.monotonic() - t_total) * 1000
    latency['total_ms'] = total_ms
    response.latency_breakdown = latency
    retrieval_latency_ms.observe(total_ms)

    final_raw = (
        [_to_chunk_result(r).__dict__ for r in mmr_results[:10]]
        if request_body.mode == 'k_chunks'
        else []
    )
    background_tasks.add_task(
        _log_audit,
        db_pool=db_pool,
        audit_id=audit_id,
        request=request_body,
        processed_query=processed_query,
        entities=entities,
        query_embedding=query_embedding,
        dense_results=dense_results,
        sparse_results=sparse_results,
        rrf_results=rrf_results,
        mmr_results=mmr_results,
        final_results=mmr_results,
        latency_total_ms=total_ms,
        client_ip=client_ip,
        api_key_hash=api_key_hash,
    )

    # ── 11. Cache result ──────────────────────────────────────────────────────
    try:
        await redis.setex(cache_key, _RESULT_CACHE_TTL, response.model_dump_json())
    except Exception as exc:
        logger.warning(f"Failed to set result cache: {exc}")

    return response


# ──────────────────────────────────────────────────────────────────────────────
# POST /retrieve/batch  — up to 50 requests processed sequentially
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/batch",
    response_model=list[RetrievalResponse],
    summary="Batch retrieval (max 50 queries)",
)
async def retrieve_batch(
    requests_body: list[RetrievalRequest],
    background_tasks: BackgroundTasks,
    http_request: Request,
    api_key_hash: str = Depends(_require_api_key),
) -> list[RetrievalResponse]:
    if len(requests_body) > 50:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Batch size exceeds maximum of 50 requests.",
        )

    results: list[RetrievalResponse] = []
    for req in requests_body:
        # Re-use the single retrieve handler for each item in the batch
        response = await retrieve(
            request_body=req,
            background_tasks=background_tasks,
            http_request=http_request,
            api_key_hash=api_key_hash,
        )
        results.append(response)

    return results


# ──────────────────────────────────────────────────────────────────────────────
# GET /retrieve/audit/{audit_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/audit/{audit_id}",
    summary="Fetch a retrieval audit record by ID",
)
async def get_audit(
    audit_id: str,
    http_request: Request,
    api_key_hash: str = Depends(_require_api_key),
) -> JSONResponse:
    db_pool = http_request.app.state.db_pool

    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    audit_id::text,
                    query_raw,
                    query_processed,
                    entities_detected,
                    retrieval_mode,
                    k_requested,
                    n_requested,
                    latency_ms,
                    client_ip::text,
                    api_key_hash,
                    created_at::text
                FROM retrieval_audit
                WHERE audit_id = $1::uuid
                """,
                audit_id,
            )
    except Exception as exc:
        logger.error(f"Audit fetch failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch audit record.",
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit record {audit_id!r} not found.",
        )

    return JSONResponse(dict(row))
