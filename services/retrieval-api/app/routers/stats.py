"""Stats router — system-wide metrics endpoint."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from rag_shared.auth.api_key import hash_api_key
from rag_shared.config import get_settings
from rag_shared.db.repositories.document_repo import DocumentRepository
from rag_shared.db.repositories.chunk_repo import ChunkRepository
from rag_shared.db.repositories.embedding_repo import EmbeddingRepository

from app.schemas_documents import (
    BM25Stats,
    ChunkStats,
    DocumentStats,
    RetrievalStats,
    SystemStats,
)

logger = logging.getLogger(__name__)
settings = get_settings()

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(api_key: str = Security(_api_key_header)) -> str:
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Key header required")
    valid_keys = settings.get_api_keys_list()
    if api_key in valid_keys:
        return hash_api_key(api_key)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


router = APIRouter(prefix="/stats", tags=["stats"])


@router.get(
    "",
    response_model=SystemStats,
    summary="Get system-wide statistics",
)
async def get_stats(
    request: Request,
    api_key_hash: str = Depends(_require_api_key),
) -> SystemStats:
    db_pool = request.app.state.db_pool

    doc_repo = DocumentRepository(db_pool)
    chunk_repo = ChunkRepository(db_pool)

    # Document stats
    doc_by_status = await doc_repo.count_by_status()
    doc_total = sum(doc_by_status.values())

    # Chunk stats
    chunk_by_status = await chunk_repo.count_all_by_embedding_status()
    chunk_total = sum(chunk_by_status.values())

    # Embedding count (from ChromaDB)
    chroma_collection = getattr(request.app.state, "chroma_collection", None)
    if chroma_collection is not None:
        embedding_repo = EmbeddingRepository(chroma_collection)
        total_embeddings = await embedding_repo.count()
    else:
        total_embeddings = 0

    # Retrieval audit stats
    async with db_pool.acquire() as conn:
        audit_row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total_queries,
                   AVG(latency_ms) AS avg_latency_ms,
                   COUNT(*) FILTER (WHERE created_at > now() - interval '24 hours') AS queries_24h
            FROM retrieval_audit
            """
        )

    # BM25 stats
    bm25_mgr = getattr(request.app.state, "bm25_manager", None)
    bm25_index_size = bm25_mgr._index_size if bm25_mgr else 0

    return SystemStats(
        documents=DocumentStats(
            total=doc_total,
            by_status=doc_by_status,
        ),
        chunks=ChunkStats(
            total=chunk_total,
            total_embeddings=total_embeddings,
            by_embedding_status=chunk_by_status,
        ),
        retrieval=RetrievalStats(
            total_queries=audit_row["total_queries"],
            avg_latency_ms=float(audit_row["avg_latency_ms"]) if audit_row["avg_latency_ms"] else None,
            queries_last_24h=audit_row["queries_24h"],
        ),
        bm25=BM25Stats(
            index_size=bm25_index_size,
        ),
    )
