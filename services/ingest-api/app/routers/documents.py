"""
documents.py — FastAPI router for document ingestion and management endpoints.

Implements the 5 endpoints described in Section 5.2 of the RAG Pipeline Design:
  POST   /api/v1/documents/ingest
  GET    /api/v1/documents/{document_id}
  GET    /api/v1/documents/{document_id}/chunks
  DELETE /api/v1/documents/{document_id}
  GET    /api/v1/health  (defined in main.py)
"""
import hashlib
import io
import time
from typing import Optional

import aio_pika
import msgpack
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

from rag_shared.auth.api_key import api_key_header, validate_api_key, RateLimiter
from rag_shared.config import get_settings
from rag_shared.db.repositories.document_repo import DocumentRepository
from rag_shared.db.repositories.chunk_repo import ChunkRepository
from rag_shared.logging import get_logger
from rag_shared.metrics import ingest_documents_total
from rag_shared.queue.schemas import IngestionTask
from rag_shared.queue.topology import EXCHANGE_INGESTION, RK_INGEST
from rag_shared.storage.s3_client import S3Client

from ..schemas import ChunkItem, ChunksResponse, DocumentStatus, IngestResponse, ReprocessRequest

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/documents", tags=["documents"])

# PDF magic bytes: every valid PDF starts with "%PDF"
_PDF_MAGIC = b'%PDF'

# Redis SHA-256 dedup key TTL (7 days in seconds)
_SHA256_TTL_SECONDS = 7 * 24 * 3600


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _build_s3_client() -> S3Client:
    """Instantiate an S3Client from the current settings."""
    return S3Client(
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        endpoint_url=settings.s3_endpoint_url,
    )


async def _authenticate(
    request: Request,
    api_key: str = Security(api_key_header),
) -> str:
    """
    FastAPI dependency that:
      1. Validates the X-API-Key header against configured keys.
      2. Enforces per-API-key rate limit (1000 req/min).
      3. Enforces per-IP rate limit (50 req/min).

    Returns the short hash of the API key for audit logging.
    """
    # Validate API key
    key_hash = await validate_api_key(api_key, valid_keys=settings.get_api_keys_list())

    redis = request.app.state.redis

    # Per-API-key sliding window rate limit
    key_limiter = RateLimiter(
        redis_client=redis,
        limit=settings.rate_limit_per_minute,
        window_seconds=60,
    )
    await key_limiter.enforce(identifier=f"apikey:{key_hash}", name="API key")

    # Per-IP sliding window rate limit
    client_ip = (request.client.host if request.client else "unknown")
    ip_limiter = RateLimiter(
        redis_client=redis,
        limit=settings.rate_limit_per_ip,
        window_seconds=60,
    )
    await ip_limiter.enforce(identifier=f"ip:{client_ip}", name="IP")

    return key_hash


# ──────────────────────────────────────────────────────────────────────────────
# POST /documents/ingest
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestResponse,
    summary="Upload a PDF document for ingestion",
    responses={
        202: {"description": "Document accepted for ingestion"},
        400: {"description": "Bad request (invalid file type or size)"},
        401: {"description": "Missing API key"},
        403: {"description": "Invalid API key"},
        409: {"description": "Duplicate document"},
        413: {"description": "File too large"},
        415: {"description": "Unsupported media type"},
        429: {"description": "Rate limit exceeded"},
        502: {"description": "Upstream storage error"},
    },
)
async def ingest_document(
    request: Request,
    file: UploadFile = File(..., description="PDF document to ingest"),
    key_hash: str = Depends(_authenticate),
) -> IngestResponse:
    """
    Accepts a PDF document and queues it for the ingestion pipeline.

    Processing order:
      1. Validate MIME type via PDF magic bytes.
      2. Validate file size against MAX_FILE_SIZE_MB.
      3. Compute SHA-256 of the file content.
      4. Check Redis for a duplicate by SHA-256 hash.
      5. Fall back to a Postgres duplicate check if Redis misses.
      6. Stream-upload the file to S3/MinIO.
      7. Insert a parent_documents row in Postgres.
      8. Publish a persistent IngestionTask to RabbitMQ.
      9. Cache SHA-256 => document_id in Redis (TTL 7 days).
      10. Return 202 Accepted.
    """
    # ── 1. Buffer the upload so we can inspect magic bytes and hash it ─────────
    content: bytes = await file.read()

    # ── 2. MIME validation via magic bytes ─────────────────────────────────────
    if content[:4] != _PDF_MAGIC:
        ingest_documents_total.labels(status="invalid").inc()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Unsupported file type. Only PDF files are accepted. "
                "The uploaded file does not start with the PDF header (%PDF)."
            ),
        )

    # ── 3. File size validation ────────────────────────────────────────────────
    max_bytes: int = settings.max_file_size_mb * 1024 * 1024
    actual_mb: float = len(content) / (1024 ** 2)
    if len(content) > max_bytes:
        ingest_documents_total.labels(status="invalid").inc()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File size {actual_mb:.1f} MB exceeds the maximum allowed size "
                f"of {settings.max_file_size_mb} MB."
            ),
        )

    # ── 4. Compute SHA-256 ─────────────────────────────────────────────────────
    sha256_hash: str = hashlib.sha256(content).hexdigest()
    redis_dedup_key: str = f"doc:sha256:{sha256_hash}"
    redis = request.app.state.redis

    # ── 5. Redis deduplication check ──────────────────────────────────────────
    cached_id: Optional[str] = await redis.get(redis_dedup_key)
    if cached_id:
        logger.info(
            "Duplicate document detected (Redis)",
            sha256=sha256_hash,
            document_id=cached_id,
        )
        ingest_documents_total.labels(status="duplicate").inc()
        return IngestResponse(
            document_id=cached_id,
            status="duplicate",
            message="Document already exists. Returning the original document ID.",
        )

    # ── 6. DB deduplication fallback ──────────────────────────────────────────
    doc_repo = DocumentRepository(request.app.state.db_pool)
    db_duplicate_id: Optional[str] = await doc_repo.get_by_sha256(sha256_hash)
    if db_duplicate_id:
        # Re-seed cache to avoid future DB round-trips
        await redis.set(redis_dedup_key, db_duplicate_id, ex=_SHA256_TTL_SECONDS)
        logger.info(
            "Duplicate document detected (DB fallback)",
            sha256=sha256_hash,
            document_id=db_duplicate_id,
        )
        ingest_documents_total.labels(status="duplicate").inc()
        return IngestResponse(
            document_id=db_duplicate_id,
            status="duplicate",
            message="Document already exists. Returning the original document ID.",
        )

    # ── 7. Streaming S3 upload ─────────────────────────────────────────────────
    safe_filename: str = file.filename or "document.pdf"
    # Use SHA-256 as part of the key to guarantee uniqueness in S3
    s3_key: str = f"documents/{sha256_hash}/{safe_filename}"
    s3_client = _build_s3_client()

    try:
        s3_uri: str = await s3_client.upload_file_streaming(
            file_obj=io.BytesIO(content),
            bucket=settings.s3_bucket,
            key=s3_key,
            content_type="application/pdf",
        )
    except Exception as exc:
        logger.error(
            "S3 upload failed",
            filename=safe_filename,
            error=str(exc),
        )
        ingest_documents_total.labels(status="failed").inc()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Document storage upload failed: {exc}",
        )

    # ── 8. Insert parent_documents row ─────────────────────────────────────────
    try:
        document_id: str = await doc_repo.create(
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
    except Exception as exc:
        logger.error(
            "Database insert failed",
            filename=safe_filename,
            sha256=sha256_hash,
            error=str(exc),
        )
        ingest_documents_total.labels(status="failed").inc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to record document metadata: {exc}",
        )

    # ── 9. Publish IngestionTask to RabbitMQ ──────────────────────────────────
    ingestion_task = IngestionTask(
        parent_document_id=document_id,
        s3_bucket=settings.s3_bucket,
        s3_key=s3_key,
        filename=safe_filename,
        file_size_bytes=len(content),
        mime_type="application/pdf",
        source_metadata={"sha256_hash": sha256_hash},
    )
    task_body: bytes = msgpack.packb({
        "parent_document_id": ingestion_task.parent_document_id,
        "s3_bucket": ingestion_task.s3_bucket,
        "s3_key": ingestion_task.s3_key,
        "filename": ingestion_task.filename,
        "file_size_bytes": ingestion_task.file_size_bytes,
        "mime_type": ingestion_task.mime_type,
        "priority": ingestion_task.priority,
        "retry_count": ingestion_task.retry_count,
        "created_at": ingestion_task.created_at,
        "source_metadata": ingestion_task.source_metadata,
    })

    try:
        channel = request.app.state.rabbit_channel
        # Idempotently (re-)declare the exchange before publishing
        exchange = await channel.declare_exchange(
            EXCHANGE_INGESTION,
            type=aio_pika.ExchangeType.DIRECT,
            durable=True,
            passive=False,
        )
        amqp_message = aio_pika.Message(
            body=task_body,
            content_type="application/msgpack",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=document_id,
        )
        await exchange.publish(amqp_message, routing_key=RK_INGEST)
        logger.info(
            "IngestionTask published",
            document_id=document_id,
            exchange=EXCHANGE_INGESTION,
            routing_key=RK_INGEST,
        )
    except Exception as exc:
        # RabbitMQ publish failure is non-fatal here: the document is already persisted
        # in Postgres and can be requeued by an operator or a periodic sweep job.
        logger.error(
            "RabbitMQ publish failed — document persisted but NOT queued",
            document_id=document_id,
            error=str(exc),
        )

    # ── 10. Cache SHA-256 -> document_id (TTL 7 days) ─────────────────────────
    try:
        await redis.set(redis_dedup_key, document_id, ex=_SHA256_TTL_SECONDS)
    except Exception as exc:
        # Cache failure is non-fatal
        logger.warning(
            "Redis dedup cache set failed",
            document_id=document_id,
            error=str(exc),
        )

    logger.info(
        "Document accepted for ingestion",
        document_id=document_id,
        filename=safe_filename,
        size_bytes=len(content),
        sha256=sha256_hash,
    )
    ingest_documents_total.labels(status="success").inc()

    return IngestResponse(
        document_id=document_id,
        status="accepted",
        message="Document accepted and queued for ingestion.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# GET /documents/{document_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{document_id}",
    response_model=DocumentStatus,
    summary="Get document status and metadata",
)
async def get_document(
    document_id: str,
    request: Request,
    key_hash: str = Depends(_authenticate),
) -> DocumentStatus:
    """Return the current processing status and metadata for a document."""
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    s3_uri: Optional[str] = None
    if doc.get("s3_bucket") and doc.get("s3_key"):
        s3_uri = f"s3://{doc['s3_bucket']}/{doc['s3_key']}"

    return DocumentStatus(
        document_id=str(doc["parent_document_id"]),
        filename=doc["filename"],
        status=doc["status"],
        page_count=doc.get("page_count"),
        has_text=bool(doc.get("has_text", False)),
        has_images=bool(doc.get("has_images", False)),
        language_detected=doc.get("language_detected"),
        s3_uri=s3_uri,
        error_message=doc.get("error_message"),
        retry_count=doc.get("retry_count", 0),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
        completed_at=doc.get("completed_at"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# GET /documents/{document_id}/chunks
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{document_id}/chunks",
    response_model=ChunksResponse,
    summary="List chunks for a document with pagination",
)
async def get_document_chunks(
    document_id: str,
    request: Request,
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of chunks to return per page",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of chunks to skip (for pagination)",
    ),
    key_hash: str = Depends(_authenticate),
) -> ChunksResponse:
    """
    Return a paginated list of text chunks extracted from the document.

    Chunks are ordered by chunk_index ascending. Use `limit` and `offset`
    for page-based navigation.
    """
    # Verify the document exists before fetching chunks
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    chunk_repo = ChunkRepository(request.app.state.db_pool)
    all_chunks = await chunk_repo.list_by_document(document_id)

    total: int = len(all_chunks)
    page_slice = all_chunks[offset: offset + limit]

    chunk_items = [
        ChunkItem(
            chunk_id=str(c["chunk_id"]),
            chunk_index=c["chunk_index"],
            chunk_text=c["chunk_text"],
            page_number=c.get("page_number"),
            source_type="text",
            # word_count is the closest available proxy for token count
            token_count=c.get("word_count"),
            embedding_status=c.get("status", "pending"),
        )
        for c in page_slice
    ]

    return ChunksResponse(
        document_id=document_id,
        total_chunks=total,
        chunks=chunk_items,
    )


# ──────────────────────────────────────────────────────────────────────────────
# DELETE /documents/{document_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.delete(
    "/{document_id}",
    status_code=status.HTTP_200_OK,
    summary="Soft-delete a document and hard-delete its chunks",
    responses={
        200: {"description": "Document deleted"},
        404: {"description": "Document not found"},
    },
)
async def delete_document(
    document_id: str,
    request: Request,
    key_hash: str = Depends(_authenticate),
) -> dict:
    """
    Soft-delete a document and hard-delete all its chunks.

    Sets the document status to 'failed' (with error_message='deleted') in
    Postgres. Hard-deletes all associated chunks and their embeddings from the
    `chunks` and `chunk_embeddings` tables. Also deletes the associated object
    from S3/MinIO and evicts the SHA-256 dedup key from Redis.
    """
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    # Soft-delete in Postgres (status -> 'failed', error_message -> 'deleted')
    deleted: bool = await doc_repo.soft_delete(document_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to soft-delete document record in the database.",
        )

    # Hard-delete all chunks and their embeddings (best-effort)
    chunk_repo = ChunkRepository(request.app.state.db_pool)
    try:
        chunks_deleted = await chunk_repo.delete_by_document(document_id)
    except Exception as exc:
        logger.warning(
            "Chunk cascade-delete failed after DB soft-delete (non-fatal)",
            document_id=document_id,
            error=str(exc),
        )
        chunks_deleted = 0

    # Delete from S3/MinIO (best-effort; log warning on failure, do not abort)
    s3_bucket: Optional[str] = doc.get("s3_bucket")
    s3_key: Optional[str] = doc.get("s3_key")
    if s3_bucket and s3_key:
        s3_client = _build_s3_client()
        try:
            await s3_client.delete_file(bucket=s3_bucket, key=s3_key)
        except Exception as exc:
            logger.warning(
                "S3 delete failed after DB soft-delete (non-fatal)",
                document_id=document_id,
                s3_uri=f"s3://{s3_bucket}/{s3_key}",
                error=str(exc),
            )

    # Evict SHA-256 dedup key from Redis (best-effort)
    sha256_hash: Optional[str] = doc.get("sha256_hash")
    if sha256_hash:
        try:
            await request.app.state.redis.delete(f"doc:sha256:{sha256_hash}")
        except Exception as exc:
            logger.warning(
                "Redis dedup key eviction failed (non-fatal)",
                document_id=document_id,
                error=str(exc),
            )

    logger.info("Document deleted", document_id=document_id, chunks_deleted=chunks_deleted)
    return {
        "document_id": document_id,
        "status": "deleted",
        "message": f"Document and {chunks_deleted} chunk(s) have been deleted.",
    }


# ──────────────────────────────────────────────────────────────────────────────
# POST /documents/{document_id}/reprocess
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{document_id}/reprocess",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reprocess a document with new chunking parameters",
    responses={
        202: {"description": "Document accepted for reprocessing"},
        404: {"description": "Document not found"},
        409: {"description": "Document is currently being processed"},
    },
)
async def reprocess_document(
    document_id: str,
    body: ReprocessRequest,
    request: Request,
    key_hash: str = Depends(_authenticate),
) -> dict:
    """
    Re-ingest an existing document with new parameters.

    Deletes all existing chunks/embeddings, resets the document to 'pending',
    and re-publishes an ingestion task with override chunking parameters.
    The original S3 PDF is re-used — no re-upload is required.

    Blocked if the document is currently mid-processing (status='ingesting',
    'chunking', or 'embedding').
    """
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    # Block reprocess while the document is mid-pipeline
    _BLOCKED_STATUSES = {"ingesting", "chunking", "embedding"}
    if doc.get("status") in _BLOCKED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Document is currently being processed (status='{doc['status']}'). "
                "Wait for it to complete or fail before reprocessing."
            ),
        )

    # 1. Hard-delete existing chunks and embeddings
    chunk_repo = ChunkRepository(request.app.state.db_pool)
    try:
        chunks_deleted = await chunk_repo.delete_by_document(document_id)
    except Exception as exc:
        logger.warning(
            "Chunk pre-delete failed before reprocess (non-fatal)",
            document_id=document_id,
            error=str(exc),
        )
        chunks_deleted = 0

    # 2. Reset document back to pending state
    await doc_repo.reset_for_reprocess(document_id)

    # 3. Publish new ingestion task with chunking override parameters
    task_body: bytes = msgpack.packb({
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
        # Per-run chunking overrides (read by ingestion-worker)
        "chunk_max_tokens": body.chunk_max_tokens,
        "chunk_overlap_tokens": body.chunk_overlap_tokens,
        "chunking_strategy": body.chunking_strategy,
        "force_ocr": body.force_ocr,
        "ocr_languages": body.ocr_languages,
    })

    try:
        channel = request.app.state.rabbit_channel
        exchange = await channel.declare_exchange(
            EXCHANGE_INGESTION,
            type=aio_pika.ExchangeType.DIRECT,
            durable=True,
            passive=False,
        )
        amqp_message = aio_pika.Message(
            body=task_body,
            content_type="application/msgpack",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=document_id,
        )
        await exchange.publish(amqp_message, routing_key=RK_INGEST)
        logger.info(
            "Reprocess task published",
            document_id=document_id,
            chunks_cleared=chunks_deleted,
            chunk_max_tokens=body.chunk_max_tokens,
            chunk_overlap_tokens=body.chunk_overlap_tokens,
            chunking_strategy=body.chunking_strategy,
            force_ocr=body.force_ocr,
        )
    except Exception as exc:
        logger.error(
            "RabbitMQ publish failed for reprocess — document reset to pending but NOT queued",
            document_id=document_id,
            error=str(exc),
        )

    return {
        "document_id": document_id,
        "status": "accepted",
        "chunks_cleared": chunks_deleted,
        "message": (
            f"Document requeued for reprocessing with "
            f"chunk_max_tokens={body.chunk_max_tokens}, "
            f"overlap={body.chunk_overlap_tokens}, "
            f"strategy='{body.chunking_strategy}', "
            f"force_ocr={body.force_ocr}."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# POST /documents/{document_id}/hold
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{document_id}/hold",
    status_code=status.HTTP_200_OK,
    summary="Place a document on hold, stopping further processing",
    responses={
        200: {"description": "Document placed on hold"},
        404: {"description": "Document not found"},
        409: {"description": "Document cannot be placed on hold in its current state"},
    },
)
async def hold_document(
    document_id: str,
    request: Request,
    key_hash: str = Depends(_authenticate),
) -> dict:
    """
    Stop processing for a document and place it on hold.

    Only documents with status 'pending', 'ingesting', 'chunking', or
    'embedding' can be placed on hold. From the 'on_hold' state, the document
    can be deleted or reprocessed.

    Sets a Redis flag so the ingestion worker will abort processing if it picks
    up the document before the status update propagates.
    """
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    _HOLDABLE_STATUSES = {"pending", "ingesting", "chunking", "embedding"}
    current_status = doc.get("status")
    if current_status not in _HOLDABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Document cannot be placed on hold (status='{current_status}'). "
                "Only pending or in-progress documents can be held."
            ),
        )

    # Set Redis hold flag (checked by ingestion worker before each stage)
    redis = request.app.state.redis
    try:
        await redis.set(f"doc:hold:{document_id}", "1", ex=86400)
    except Exception as exc:
        logger.warning(
            "Redis hold flag set failed (non-fatal)",
            document_id=document_id,
            error=str(exc),
        )

    # Update DB status
    held = await doc_repo.hold_document(document_id)
    if not held:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update document status in the database.",
        )

    logger.info(
        "Document placed on hold",
        document_id=document_id,
        previous_status=current_status,
    )
    return {
        "document_id": document_id,
        "status": "on_hold",
        "previous_status": current_status,
        "message": f"Document placed on hold (was '{current_status}'). It can now be deleted or reprocessed.",
    }


# ──────────────────────────────────────────────────────────────────────────────
# POST /documents/{document_id}/resume
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{document_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resume processing for a held document",
    responses={
        202: {"description": "Document resumed for processing"},
        404: {"description": "Document not found"},
        409: {"description": "Document is not on hold"},
    },
)
async def resume_document(
    document_id: str,
    request: Request,
    key_hash: str = Depends(_authenticate),
) -> dict:
    """
    Resume processing for a document that was placed on hold.

    Resets the document to 'pending' status, clears the Redis hold flag,
    and re-publishes the ingestion task to the queue.
    """
    doc_repo = DocumentRepository(request.app.state.db_pool)
    doc = await doc_repo.get_by_id(document_id)

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    if doc.get("status") != "on_hold":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document is not on hold (status='{doc.get('status')}').",
        )

    # Clear Redis hold flag
    redis = request.app.state.redis
    try:
        await redis.delete(f"doc:hold:{document_id}")
    except Exception as exc:
        logger.warning(
            "Redis hold flag clear failed (non-fatal)",
            document_id=document_id,
            error=str(exc),
        )

    # Reset document to pending
    await doc_repo.reset_for_reprocess(document_id)

    # Re-publish ingestion task
    task_body: bytes = msgpack.packb({
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
    })

    try:
        channel = request.app.state.rabbit_channel
        exchange = await channel.declare_exchange(
            EXCHANGE_INGESTION,
            type=aio_pika.ExchangeType.DIRECT,
            durable=True,
            passive=False,
        )
        amqp_message = aio_pika.Message(
            body=task_body,
            content_type="application/msgpack",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=document_id,
        )
        await exchange.publish(amqp_message, routing_key=RK_INGEST)
        logger.info("Resume task published", document_id=document_id)
    except Exception as exc:
        logger.error(
            "RabbitMQ publish failed for resume — document reset to pending but NOT queued",
            document_id=document_id,
            error=str(exc),
        )

    return {
        "document_id": document_id,
        "status": "accepted",
        "message": "Document resumed and requeued for processing.",
    }
