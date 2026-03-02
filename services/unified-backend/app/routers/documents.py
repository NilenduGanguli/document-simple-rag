"""
Unified documents router.

Merges ingest-api and retrieval-api document endpoints:
  POST   /documents/ingest                  — upload and queue a PDF
  GET    /documents                         — list all documents
  GET    /documents/{id}                    — pipeline status detail
  GET    /documents/{id}/chunks             — paginated chunks
  GET    /documents/{id}/download-url       — presigned S3 URL
  DELETE /documents/{id}                    — soft-delete
  POST   /documents/{id}/reprocess          — re-ingest with new params
  POST   /documents/{id}/hold               — place on hold
  POST   /documents/{id}/resume             — resume from hold

Redis replaced with AppState caches (TTLCache + hold_flags dict).
RabbitMQ replaced with asyncio.Queue (app.state.ingestion_queue).
"""
from __future__ import annotations

import hashlib
import io
import logging
import time
from typing import Optional

import asyncpg
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Security,
    UploadFile,
    status,
)
from fastapi.security import APIKeyHeader

from rag_shared.auth.api_key import hash_api_key, validate_api_key
from rag_shared.config import get_settings
from rag_shared.db.repositories.document_repo import DocumentRepository
from rag_shared.db.repositories.chunk_repo import ChunkRepository
from rag_shared.db.repositories.embedding_repo import EmbeddingRepository
from rag_shared.metrics import ingest_documents_total
from rag_shared.storage.s3_client import S3Client

from app.schemas import (
    ChunkItem,
    ChunksResponse,
    DocumentStatus,
    IngestResponse,
    ReprocessRequest,
)
from app.schemas_documents import (
    ChunkItem as ChunkItemDetail,
    ChunksResponse as ChunksResponseDetail,
    DocumentListResponse,
    DocumentPipelineStatus,
    DocumentSummary,
    PipelineStageInfo,
    PresignedUrlResponse,
)
from app.state import RateLimitExceeded

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/documents", tags=["documents"])

_PDF_MAGIC = b'%PDF'
_SHA256_TTL_SECONDS = 7 * 24 * 3600


# ── Auth helper ───────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _authenticate(
    api_key: str = Security(_api_key_header),
    request: Request = None,
) -> str:
    """Validate API key and enforce rate limits (in-memory)."""
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Key header required")

    key_hash = await validate_api_key(api_key, valid_keys=settings.get_api_keys_list())

    rate_limiter = request.app.state.rate_limiter
    try:
        rate_limiter.enforce(
            key=f"apikey:{key_hash}",
            limit=settings.rate_limit_per_minute,
            window_seconds=60,
            name="API key",
        )
        client_ip = request.client.host if request.client else "unknown"
        rate_limiter.enforce(
            key=f"ip:{client_ip}",
            limit=settings.rate_limit_per_ip,
            window_seconds=60,
            name="IP",
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))

    return key_hash


def _require_api_key(api_key: str = Security(_api_key_header)) -> str:
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Key header required")
    valid_keys = settings.get_api_keys_list()
    if api_key in valid_keys:
        return hash_api_key(api_key)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


def _build_s3_client() -> S3Client:
    return S3Client(
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        endpoint_url=settings.s3_endpoint_url,
    )


# ── Pipeline stage computation ────────────────────────────────────────────────

_STAGE_DEFS = [
    ("upload", "Upload"),
    ("s3_storage", "S3 Storage"),
    ("queue", "In-Process Queue"),
    ("ingestion", "Ingestion Worker"),
    ("chunking", "Text Chunking"),
    ("embedding", "ONNX Embedding"),
    ("ready", "Ready"),
]

_STATUS_TO_ACTIVE_IDX = {
    "pending": 2,
    "ingesting": 3,
    "chunking": 4,
    "embedding": 5,
    "ready": 6,
}

_STAGE_MODELS = {
    "ingestion": "PyMuPDF + OpenAI Vision OCR",
    "chunking": "RecursiveCharacterSplitter (BERT 512 tokens)",
    "embedding": "bert-base-multilingual-cased (ONNX INT8)",
}


def _compute_pipeline_stages(doc: dict) -> list:
    doc_status = doc.get("status", "pending")
    is_failed = doc_status == "failed"
    active_idx = _STATUS_TO_ACTIVE_IDX.get(doc_status, 0)

    stages = []
    for i, (name, label) in enumerate(_STAGE_DEFS):
        if is_failed:
            stage_status = "completed" if i < active_idx else ("failed" if i == active_idx else "pending")
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


# ── POST /documents/ingest ────────────────────────────────────────────────────

@router.post(
    "/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestResponse,
    summary="Upload a PDF document for ingestion",
)
async def ingest_document(
    request: Request,
    file: UploadFile = File(...),
    key_hash: str = Depends(_authenticate),
) -> IngestResponse:
    content: bytes = await file.read()

    if content[:4] != _PDF_MAGIC:
        ingest_documents_total.labels(status="invalid").inc()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type. Only PDF files are accepted.",
        )

    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        ingest_documents_total.labels(status="invalid").inc()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds the maximum of {settings.max_file_size_mb} MB.",
        )

    sha256_hash = hashlib.sha256(content).hexdigest()
    app_state = request.app.state

    # ── Dedup check (in-memory cache) ────────────────────────────────────
    cached_id = app_state.get_dedup(sha256_hash)
    if cached_id:
        ingest_documents_total.labels(status="duplicate").inc()
        return IngestResponse(
            document_id=cached_id,
            status="duplicate",
            message="Document already exists. Returning the original document ID.",
        )

    doc_repo = DocumentRepository(app_state.db_pool)
    db_duplicate_id = await doc_repo.get_by_sha256(sha256_hash)
    if db_duplicate_id:
        app_state.set_dedup(sha256_hash, db_duplicate_id)
        ingest_documents_total.labels(status="duplicate").inc()
        return IngestResponse(
            document_id=db_duplicate_id,
            status="duplicate",
            message="Document already exists. Returning the original document ID.",
        )

    # ── S3 upload ─────────────────────────────────────────────────────────
    safe_filename = file.filename or "document.pdf"
    s3_key = f"documents/{sha256_hash}/{safe_filename}"
    s3_client = _build_s3_client()

    try:
        s3_uri = await s3_client.upload_file_streaming(
            file_obj=io.BytesIO(content),
            bucket=settings.s3_bucket,
            key=s3_key,
            content_type="application/pdf",
        )
    except Exception as exc:
        ingest_documents_total.labels(status="failed").inc()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Document storage upload failed: {exc}",
        )

    # ── Insert parent_documents row ───────────────────────────────────────
    try:
        document_id = await doc_repo.create(
            filename=safe_filename,
            s3_bucket=settings.s3_bucket,
            s3_key=s3_key,
            file_size_bytes=len(content),
            mime_type="application/pdf",
            sha256_hash=sha256_hash,
            source_metadata={
                "original_filename": safe_filename,
                "upload_api_key_hash": key_hash,
                "s3_uri": s3_uri,
            },
        )
    except asyncpg.UniqueViolationError:
        existing_id = await doc_repo.get_by_sha256(sha256_hash)
        if existing_id:
            app_state.set_dedup(sha256_hash, existing_id)
        ingest_documents_total.labels(status="duplicate").inc()
        return IngestResponse(
            document_id=existing_id or "unknown",
            status="duplicate",
            message="Document already exists (detected during concurrent upload).",
        )
    except Exception as exc:
        ingest_documents_total.labels(status="failed").inc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to record document metadata: {exc}",
        )

    # ── Queue ingestion task ──────────────────────────────────────────────
    task_dict = {
        "parent_document_id": document_id,
        "s3_bucket": settings.s3_bucket,
        "s3_key": s3_key,
        "filename": safe_filename,
        "file_size_bytes": len(content),
        "mime_type": "application/pdf",
        "priority": 0,
        "retry_count": 0,
        "created_at": time.time(),
        "source_metadata": {"sha256_hash": sha256_hash},
    }

    try:
        app_state.ingestion_queue.put_nowait(task_dict)
    except Exception as exc:
        logger.error(f"Failed to queue ingestion task for {document_id}: {exc}")

    # ── Cache dedup mapping ───────────────────────────────────────────────
    app_state.set_dedup(sha256_hash, document_id)

    logger.info(f"Document accepted: {document_id}, file={safe_filename}, size={len(content)}")
    ingest_documents_total.labels(status="success").inc()

    return IngestResponse(
        document_id=document_id,
        status="accepted",
        message="Document accepted and queued for ingestion.",
    )


# ── GET /documents ────────────────────────────────────────────────────────────

@router.get("", response_model=DocumentListResponse, summary="List all documents")
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

@router.get("/{document_id}", response_model=DocumentPipelineStatus, summary="Get document pipeline status")
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

@router.get("/{document_id}/chunks", response_model=ChunksResponseDetail, summary="List chunks")
async def get_document_chunks(
    document_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    api_key_hash: str = Depends(_require_api_key),
) -> ChunksResponseDetail:
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document '{document_id}' not found.")

    chunk_repo = ChunkRepository(request.app.state.db_pool)
    all_chunks = await chunk_repo.list_by_document(document_id)
    total = len(all_chunks)
    page_slice = all_chunks[offset:offset + limit]

    chunk_items = [
        ChunkItemDetail(
            chunk_id=str(c["chunk_id"]),
            chunk_index=c["chunk_index"],
            chunk_text=c["chunk_text"],
            page_number=c.get("page_number"),
            source_type=c.get("source_type", "text"),
            token_count=c.get("token_count") or c.get("word_count"),
            embedding_status=c.get("embedding_status", "pending"),
        )
        for c in page_slice
    ]

    return ChunksResponseDetail(document_id=document_id, total_chunks=total, chunks=chunk_items)


# ── GET /documents/{document_id}/download-url ─────────────────────────────────

@router.get("/{document_id}/download-url", response_model=PresignedUrlResponse, summary="Get presigned download URL")
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

    if settings.s3_external_url and settings.s3_endpoint_url:
        url = url.replace(settings.s3_endpoint_url, settings.s3_external_url)

    return PresignedUrlResponse(
        document_id=document_id,
        url=url,
        expires_in=expires,
        filename=doc["filename"],
    )


# ── DELETE /documents/{document_id} ──────────────────────────────────────────

@router.delete("/{document_id}", status_code=status.HTTP_200_OK, summary="Delete a document")
async def delete_document(
    document_id: str,
    request: Request,
    key_hash: str = Depends(_authenticate),
) -> dict:
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)

    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document '{document_id}' not found.")

    deleted = await doc_repo.soft_delete(document_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete document.")

    chunk_repo = ChunkRepository(request.app.state.db_pool)
    try:
        chunks_deleted = await chunk_repo.delete_by_document(document_id)
    except Exception as exc:
        logger.warning(f"Chunk delete failed (non-fatal): {exc}")
        chunks_deleted = 0

    embedding_repo = EmbeddingRepository(request.app.state.db_pool)
    await embedding_repo.delete_by_document(document_id)

    s3_bucket = doc.get("s3_bucket")
    s3_key = doc.get("s3_key")
    if s3_bucket and s3_key:
        s3_client = _build_s3_client()
        try:
            await s3_client.delete_file(bucket=s3_bucket, key=s3_key)
        except Exception as exc:
            logger.warning(f"S3 delete failed (non-fatal): {exc}")

    sha256_hash = doc.get("sha256_hash")
    if sha256_hash:
        request.app.state.evict_dedup(sha256_hash)

    logger.info(f"Document deleted: {document_id}, chunks={chunks_deleted}")
    return {
        "document_id": document_id,
        "status": "deleted",
        "message": f"Document and {chunks_deleted} chunk(s) have been deleted.",
    }


# ── POST /documents/{document_id}/reprocess ───────────────────────────────────

@router.post("/{document_id}/reprocess", status_code=status.HTTP_202_ACCEPTED, summary="Reprocess a document")
async def reprocess_document(
    document_id: str,
    body: ReprocessRequest,
    request: Request,
    key_hash: str = Depends(_authenticate),
) -> dict:
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)

    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document '{document_id}' not found.")

    _BLOCKED_STATUSES = {"ingesting", "chunking", "embedding"}
    if doc.get("status") in _BLOCKED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document is currently being processed (status='{doc['status']}').",
        )

    chunk_repo = ChunkRepository(request.app.state.db_pool)
    try:
        chunks_deleted = await chunk_repo.delete_by_document(document_id)
    except Exception as exc:
        logger.warning(f"Chunk pre-delete failed (non-fatal): {exc}")
        chunks_deleted = 0

    embedding_repo = EmbeddingRepository(request.app.state.db_pool)
    await embedding_repo.delete_by_document(document_id)

    await doc_repo.reset_for_reprocess(document_id)

    task_dict = {
        "parent_document_id": document_id,
        "s3_bucket": doc["s3_bucket"],
        "s3_key": doc["s3_key"],
        "filename": doc["filename"],
        "file_size_bytes": doc.get("file_size_bytes", 0),
        "mime_type": doc.get("mime_type", "application/pdf"),
        "priority": 1,
        "retry_count": 0,
        "created_at": time.time(),
        "source_metadata": doc.get("source_metadata") or {},
        "chunk_max_tokens": body.chunk_max_tokens,
        "chunk_overlap_tokens": body.chunk_overlap_tokens,
        "chunking_strategy": body.chunking_strategy,
        "force_ocr": body.force_ocr,
    }

    try:
        request.app.state.ingestion_queue.put_nowait(task_dict)
    except Exception as exc:
        logger.error(f"Failed to queue reprocess task for {document_id}: {exc}")

    return {
        "document_id": document_id,
        "status": "accepted",
        "chunks_cleared": chunks_deleted,
        "message": f"Document requeued with chunk_max_tokens={body.chunk_max_tokens}, force_ocr={body.force_ocr}.",
    }


# ── POST /documents/{document_id}/hold ───────────────────────────────────────

@router.post("/{document_id}/hold", status_code=status.HTTP_200_OK, summary="Place document on hold")
async def hold_document(
    document_id: str,
    request: Request,
    key_hash: str = Depends(_authenticate),
) -> dict:
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)

    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document '{document_id}' not found.")

    _HOLDABLE_STATUSES = {"pending", "ingesting", "chunking", "embedding"}
    current_status = doc.get("status")
    if current_status not in _HOLDABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document cannot be placed on hold (status='{current_status}').",
        )

    request.app.state.set_hold(document_id)
    held = await doc_repo.hold_document(document_id)
    if not held:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update document status.")

    logger.info(f"Document placed on hold: {document_id}")
    return {
        "document_id": document_id,
        "status": "on_hold",
        "previous_status": current_status,
        "message": f"Document placed on hold (was '{current_status}').",
    }


# ── POST /documents/{document_id}/resume ──────────────────────────────────────

@router.post("/{document_id}/resume", status_code=status.HTTP_202_ACCEPTED, summary="Resume a held document")
async def resume_document(
    document_id: str,
    request: Request,
    key_hash: str = Depends(_authenticate),
) -> dict:
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)

    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document '{document_id}' not found.")

    if doc.get("status") != "on_hold":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document is not on hold (status='{doc.get('status')}').",
        )

    request.app.state.clear_hold(document_id)
    await doc_repo.reset_for_reprocess(document_id)

    task_dict = {
        "parent_document_id": document_id,
        "s3_bucket": doc["s3_bucket"],
        "s3_key": doc["s3_key"],
        "filename": doc["filename"],
        "file_size_bytes": doc.get("file_size_bytes", 0),
        "mime_type": doc.get("mime_type", "application/pdf"),
        "priority": 1,
        "retry_count": 0,
        "created_at": time.time(),
        "source_metadata": doc.get("source_metadata") or {},
    }

    try:
        request.app.state.ingestion_queue.put_nowait(task_dict)
    except Exception as exc:
        logger.error(f"Failed to queue resume task for {document_id}: {exc}")

    logger.info(f"Document resumed: {document_id}")
    return {
        "document_id": document_id,
        "status": "accepted",
        "message": "Document resumed and requeued for processing.",
    }
