"""
Unified backend — single FastAPI application.

Replaces: ingest-api, ingestion-worker, ocr-api, embedding-service, retrieval-api.
No Redis, no RabbitMQ. All state in-process.

Lifespan:
  1. Connect to PostgreSQL (asyncpg pool)
  2. Connect to ChromaDB
  3. Load ONNX models (bi-encoder, optional cross-encoder, optional NER)
  4. Build BM25 index
  5. Start BM25 refresh background task
  6. Start ingestion worker pool (asyncio background tasks)
  7. Set up MinIO S3 client

Endpoints:
  POST /api/v1/documents/ingest             — upload PDF
  GET  /api/v1/documents                    — list documents
  GET  /api/v1/documents/{id}               — pipeline status
  GET  /api/v1/documents/{id}/chunks
  GET  /api/v1/documents/{id}/download-url
  DELETE /api/v1/documents/{id}
  POST /api/v1/documents/{id}/reprocess
  POST /api/v1/documents/{id}/hold
  POST /api/v1/documents/{id}/resume
  POST /api/v1/retrieve                     — hybrid retrieval
  POST /api/v1/retrieve/batch
  GET  /api/v1/retrieve/audit/{id}
  POST /api/v1/auth/login
  GET  /api/v1/auth/me
  GET  /api/v1/auth/config
  GET  /api/v1/stats
  GET  /api/v1/health
  GET  /metrics
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from transformers import BertTokenizerFast

from rag_shared.config import get_settings
from rag_shared.db.pool import create_pool, close_pool
from rag_shared.db.chroma_client import get_chroma_client, get_embedding_collection, close_chroma
from rag_shared.logging.setup import configure_structlog
from rag_shared.metrics import get_metrics_app
from rag_shared.onnx.session_pool import ONNXSessionPool
from rag_shared.storage.s3_client import S3Client
from rag_shared.tracing.otel import configure_tracer

from app.state import AppState
from app.bm25_manager import BM25Manager
from app.pipeline.reranker import build_reranker
from app.pipeline.query_preprocessor import build_query_preprocessor
from app.workers.ingestion import IngestionWorkerPool
from app.routers.documents import router as documents_router
from app.routers.retrieve import router as retrieve_router
from app.routers.auth import router as auth_router
from app.routers.stats import router as stats_router

logger = logging.getLogger(__name__)
settings = get_settings()

MODEL_BASE = Path(os.getenv('MODEL_DEST', '/models'))
TOKENIZER_PATH = str(MODEL_BASE / 'embedding' / 'int8')

_ready = False


def _warm_up_pool(pool: ONNXSessionPool, label: str) -> None:
    """Warmup all sessions in the pool with a dummy inference."""
    import numpy as np
    dummy_ids = np.zeros((1, 128), dtype=np.int64)
    dummy_mask = np.ones((1, 128), dtype=np.int64)
    dummy_types = np.zeros((1, 128), dtype=np.int64)

    for session in pool._sessions:
        try:
            session.run(None, {
                'input_ids': dummy_ids,
                'attention_mask': dummy_mask,
                'token_type_ids': dummy_types,
            })
        except Exception as exc:
            logger.warning(f"[{label}] Warmup failed (non-fatal): {exc}")
    logger.info(f"[{label}] ONNX pool warmed up: {len(pool._sessions)} sessions")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ready

    configure_structlog(service_name='unified-backend')
    configure_tracer(settings.otel_service_name or "unified-backend", settings.jaeger_endpoint)
    logger.info("Unified backend starting up")

    # Instantiate AppState
    state = AppState()
    # Copy all state attributes to app.state via setattr so values go into
    # Starlette's _state dict (not __dict__).  Using __dict__.update() would
    # put the initial None values into __dict__, causing Python's attribute
    # lookup to return those stale Nones instead of later-assigned real values.
    for _name, _value in vars(state).items():
        setattr(app.state, _name, _value)
    # Keep reference to the AppState object itself
    app.state._app_state = state
    # Bind AppState helper methods on app.state so routers can call
    # request.app.state.get_dedup() etc.  Methods are class attributes and
    # therefore NOT included in vars(state), so bind them explicitly.
    for _m in ('get_dedup', 'set_dedup', 'evict_dedup', 'set_hold', 'clear_hold', 'is_on_hold'):
        setattr(app.state, _m, getattr(state, _m))

    # ── 1. Database pool ────────────────────────────────────────────────────
    db_pool = await create_pool(dsn=settings.database_url, min_size=5, max_size=20)
    app.state.db_pool = db_pool
    state.db_pool = db_pool

    # ── 2. ChromaDB client ───────────────────────────────────────────────────
    chroma_client = await get_chroma_client(settings.chromadb_url)
    chroma_collection = await get_embedding_collection(chroma_client)
    app.state.chroma_collection = chroma_collection
    state.chroma_collection = chroma_collection

    # ── 3. ONNX models ────────────────────────────────────────────────────────

    # Bi-encoder (required for embedding and retrieval)
    biencoder_onnx = MODEL_BASE / 'embedding' / 'int8' / 'model.onnx'
    if biencoder_onnx.exists():
        biencoder_pool = ONNXSessionPool.from_env(str(biencoder_onnx))
        _warm_up_pool(biencoder_pool, 'biencoder')
        tokenizer = BertTokenizerFast.from_pretrained(TOKENIZER_PATH)
        app.state.biencoder_pool = biencoder_pool
        app.state.biencoder_tokenizer = tokenizer
        state.biencoder_pool = biencoder_pool
        state.biencoder_tokenizer = tokenizer
        logger.info("Bi-encoder ONNX pool ready")
    else:
        logger.warning(f"Bi-encoder model not found at {biencoder_onnx}. Embedding will fail.")
        app.state.biencoder_pool = None
        app.state.biencoder_tokenizer = None

    # Cross-encoder reranker (optional)
    reranker = build_reranker(MODEL_BASE, TOKENIZER_PATH)
    app.state.reranker = reranker
    state.reranker = reranker
    if reranker:
        logger.info("Cross-encoder reranker ready")

    # NER query preprocessor (optional)
    preprocessor = build_query_preprocessor(MODEL_BASE, TOKENIZER_PATH)
    app.state.query_preprocessor = preprocessor
    state.query_preprocessor = preprocessor

    # ── 4. BM25 index ────────────────────────────────────────────────────────
    bm25_mgr = BM25Manager(db_pool)
    await bm25_mgr.build()
    app.state.bm25_manager = bm25_mgr
    state.bm25_manager = bm25_mgr

    # ── 5. S3 client ─────────────────────────────────────────────────────────
    if settings.s3_endpoint_url:
        s3_client = S3Client(
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
        )
        app.state.s3_client = s3_client
        state.s3_client = s3_client
        logger.info("S3 client initialized")
    else:
        app.state.s3_client = None
        logger.warning("S3_ENDPOINT_URL not set — S3 operations unavailable")

    # ── 6. Background tasks ───────────────────────────────────────────────────
    shutdown_event = asyncio.Event()
    app.state._shutdown_event = shutdown_event

    # BM25 periodic refresh
    bm25_task = asyncio.create_task(
        bm25_mgr.start_refresh_loop(shutdown_event), name="bm25_refresh"
    )
    app.state._bm25_task = bm25_task

    # Ingestion worker pool
    worker_pool = IngestionWorkerPool(app_state=state)
    ingestion_task = asyncio.create_task(
        worker_pool.run(shutdown_event), name="ingestion_workers"
    )
    app.state._ingestion_task = ingestion_task

    _ready = True
    logger.info("Unified backend ready")

    yield  # ← application running

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Unified backend shutting down")
    _ready = False
    shutdown_event.set()

    for task in (bm25_task, ingestion_task):
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    await close_chroma()
    await close_pool()
    logger.info("Unified backend stopped")


# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="RAG Unified Backend",
    description="Single unified server: document ingestion + retrieval + auth.",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
app.mount("/metrics", get_metrics_app())

# Routers
app.include_router(documents_router, prefix="/api/v1")
app.include_router(retrieve_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(stats_router, prefix="/api/v1")

# OpenTelemetry auto-instrumentation (optional)
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor().instrument_app(app)
except ImportError:
    pass


# ── Health endpoint ───────────────────────────────────────────────────────────

@app.get("/api/v1/health", tags=["health"], summary="Readiness probe")
async def health():
    if _ready:
        bm25_ready = (
            app.state.bm25_manager._index_size > 0
            if hasattr(app.state, 'bm25_manager') and app.state.bm25_manager
            else False
        )
        biencoder_ready = (
            app.state.biencoder_pool is not None
            if hasattr(app.state, 'biencoder_pool')
            else False
        )
        return JSONResponse({
            "status": "ok",
            "bm25_ready": bm25_ready,
            "biencoder_ready": biencoder_ready,
        })
    return JSONResponse({"status": "starting"}, status_code=503)
