"""Documents management router — list, detail, chunks, presigned download URLs."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security, status
from fastapi.security import APIKeyHeader

from rag_shared.auth.api_key import hash_api_key
from rag_shared.config import get_settings
from rag_shared.db.repositories.document_repo import DocumentRepository
from rag_shared.db.repositories.chunk_repo import ChunkRepository

from app.schemas_documents import (
    ChunkItem,
    ChunksResponse,
    DocumentListResponse,
    DocumentPipelineStatus,
    DocumentSummary,
    PipelineStageInfo,
    PresignedUrlResponse,
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


router = APIRouter(prefix="/documents", tags=["documents"])


# ── Pipeline stage computation ────────────────────────────────────────────────

_STAGE_DEFS = [
    ("upload", "Upload"),
    ("s3_storage", "S3 Storage"),
    ("queue", "Message Queue"),
    ("ingestion", "Ingestion Worker"),
    ("chunking", "Text Chunking"),
    ("embedding", "ONNX Embedding"),
    ("ready", "Ready"),
]

# Map document status to the index of the currently active stage
_STATUS_TO_ACTIVE_IDX = {
    "pending": 2,      # queued, waiting for worker
    "ingesting": 3,    # ingestion worker processing
    "chunking": 4,     # chunking active
    "embedding": 5,    # embedding active
    "ready": 6,        # all done
}

_STAGE_MODELS = {
    "ingestion": "PyMuPDF + Tesseract OCR",
    "chunking": "RecursiveCharacterSplitter (BERT 512 tokens)",
    "embedding": "bert-base-multilingual-cased (ONNX INT8)",
}


def _compute_pipeline_stages(doc: dict) -> list[PipelineStageInfo]:
    doc_status = doc.get("status", "pending")
    is_failed = doc_status == "failed"
    active_idx = _STATUS_TO_ACTIVE_IDX.get(doc_status, 0)

    stages: list[PipelineStageInfo] = []
    for i, (name, label) in enumerate(_STAGE_DEFS):
        if is_failed:
            # All stages up to last known progress are completed, current is failed
            if i < active_idx:
                stage_status = "completed"
            elif i == active_idx:
                stage_status = "failed"
            else:
                stage_status = "pending"
        elif i < active_idx:
            stage_status = "completed"
        elif i == active_idx:
            stage_status = "active" if doc_status != "ready" else "completed"
        else:
            stage_status = "pending"

        detail = None
        if name == "chunking" and stage_status in ("completed", "active"):
            total = doc.get("total_chunks", 0)
            if total:
                detail = f"{total} chunks created"
        elif name == "embedding" and stage_status in ("completed", "active"):
            done = doc.get("chunks_done", 0)
            total = doc.get("total_chunks", 0)
            if total:
                detail = f"{done}/{total} embeddings"
        elif name == "s3_storage" and stage_status == "completed":
            detail = doc.get("s3_uri") or None
        elif name == "ingestion" and stage_status in ("completed", "active"):
            parts = []
            if doc.get("has_text"):
                parts.append("text")
            if doc.get("has_images"):
                parts.append("images/OCR")
            if parts:
                detail = "Extracted: " + " + ".join(parts)
            if doc.get("page_count"):
                detail = f"{doc['page_count']} pages" + (f" ({detail})" if detail else "")

        stages.append(PipelineStageInfo(
            name=name,
            label=label,
            status=stage_status,
            detail=detail,
            model=_STAGE_MODELS.get(name),
        ))

    return stages


# ── GET /documents ────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List all documents with pagination",
)
async def list_documents(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    api_key_hash: str = Depends(_require_api_key),
) -> DocumentListResponse:
    doc_repo = DocumentRepository(request.app.state.db_pool)
    rows, total = await doc_repo.list_all(limit=limit, offset=offset, status_filter=status_filter)

    documents = [
        DocumentSummary(
            document_id=r["parent_document_id"],
            filename=r["filename"],
            status=r["status"],
            page_count=r.get("page_count"),
            file_size_bytes=r.get("file_size_bytes"),
            created_at=r.get("created_at"),
            updated_at=r.get("updated_at"),
            completed_at=r.get("completed_at"),
            chunk_count=r.get("chunk_count", 0),
            error_message=r.get("error_message"),
        )
        for r in rows
    ]

    return DocumentListResponse(documents=documents, total=total, limit=limit, offset=offset)


# ── GET /documents/{document_id} ─────────────────────────────────────────────

@router.get(
    "/{document_id}",
    response_model=DocumentPipelineStatus,
    summary="Get document details with pipeline stage info",
)
async def get_document_pipeline(
    document_id: str,
    request: Request,
    api_key_hash: str = Depends(_require_api_key),
) -> DocumentPipelineStatus:
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_pipeline_details(document_id)

    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document '{document_id}' not found.")

    s3_uri = None
    if doc.get("s3_bucket") and doc.get("s3_key"):
        s3_uri = f"s3://{doc['s3_bucket']}/{doc['s3_key']}"

    pipeline_stages = _compute_pipeline_stages({**doc, "s3_uri": s3_uri})

    return DocumentPipelineStatus(
        document_id=str(doc["parent_document_id"]),
        filename=doc["filename"],
        status=doc["status"],
        page_count=doc.get("page_count"),
        has_text=bool(doc.get("has_text", False)),
        has_images=bool(doc.get("has_images", False)),
        language_detected=doc.get("language_detected"),
        file_size_bytes=doc.get("file_size_bytes"),
        s3_uri=s3_uri,
        error_message=doc.get("error_message"),
        retry_count=doc.get("retry_count", 0),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
        completed_at=doc.get("completed_at"),
        total_chunks=doc.get("total_chunks", 0),
        chunks_pending=doc.get("chunks_pending", 0),
        chunks_processing=doc.get("chunks_processing", 0),
        chunks_done=doc.get("chunks_done", 0),
        chunks_failed=doc.get("chunks_failed", 0),
        total_embeddings=doc.get("total_embeddings", 0),
        pipeline_stages=pipeline_stages,
    )


# ── GET /documents/{document_id}/chunks ───────────────────────────────────────

@router.get(
    "/{document_id}/chunks",
    response_model=ChunksResponse,
    summary="List chunks for a document with pagination",
)
async def get_document_chunks(
    document_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    api_key_hash: str = Depends(_require_api_key),
) -> ChunksResponse:
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document '{document_id}' not found.")

    chunk_repo = ChunkRepository(request.app.state.db_pool)
    all_chunks = await chunk_repo.list_by_document(document_id)

    total = len(all_chunks)
    page_slice = all_chunks[offset: offset + limit]

    chunk_items = [
        ChunkItem(
            chunk_id=str(c["chunk_id"]),
            chunk_index=c["chunk_index"],
            chunk_text=c["chunk_text"],
            page_number=c.get("page_number"),
            source_type=c.get("source_type", "text"),
            token_count=c.get("token_count"),
            embedding_status=c.get("embedding_status", "pending"),
        )
        for c in page_slice
    ]

    return ChunksResponse(document_id=document_id, total_chunks=total, chunks=chunk_items)


# ── GET /documents/{document_id}/download-url ─────────────────────────────────

@router.get(
    "/{document_id}/download-url",
    response_model=PresignedUrlResponse,
    summary="Get a presigned S3 URL for document download",
)
async def get_download_url(
    document_id: str,
    request: Request,
    expires: int = Query(default=3600, ge=60, le=86400),
    api_key_hash: str = Depends(_require_api_key),
) -> PresignedUrlResponse:
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document '{document_id}' not found.")

    s3_bucket = doc.get("s3_bucket")
    s3_key = doc.get("s3_key")
    if not s3_bucket or not s3_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document has no S3 storage location.")

    s3_client = request.app.state.s3_client
    url = await s3_client.get_presigned_url(bucket=s3_bucket, key=s3_key, expires=expires)

    # Swap internal MinIO hostname for browser-accessible external URL
    if settings.s3_external_url and settings.s3_endpoint_url:
        url = url.replace(settings.s3_endpoint_url, settings.s3_external_url)

    return PresignedUrlResponse(
        document_id=document_id,
        url=url,
        expires_in=expires,
        filename=doc["filename"],
    )
