"""
Retrieval API — FastAPI application entry point.

Lifespan:
  1. Connect to Postgres (asyncpg pool)
  2. Connect to Redis
  3. Load ONNX models: bi-encoder, cross-encoder (optional), NER (optional)
  4. Build BM25 in-memory index
  5. Start BM25 background refresh task
  6. Register shutdown handlers

Endpoints:
  POST /api/v1/retrieve           — single hybrid retrieval
  POST /api/v1/retrieve/batch     — batch retrieval (max 50)
  GET  /api/v1/retrieve/audit/:id — fetch audit record
  GET  /api/v1/health             — readiness probe
  GET  /metrics                   — Prometheus metrics
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
from rag_shared.cache.redis_client import create_redis_client, close_redis
from rag_shared.onnx.session_pool import ONNXSessionPool
from rag_shared.logging.setup import configure_structlog
from rag_shared.metrics import get_metrics_app
from rag_shared.tracing.otel import configure_tracer
from rag_shared.storage.s3_client import S3Client

from app.bm25_manager import BM25Manager
from app.pipeline.reranker import build_reranker
from app.pipeline.query_preprocessor import build_query_preprocessor
from app.routers.retrieve import router as retrieve_router
from app.routers.documents import router as documents_router
from app.routers.stats import router as stats_router
from app.routers.auth import router as auth_router

logger = logging.getLogger(__name__)
settings = get_settings()

MODEL_BASE = Path(os.getenv('MODEL_DEST', '/models'))
# Tokenizer files are stored alongside the embedding INT8 model by model-init
TOKENIZER_PATH = str(MODEL_BASE / 'embedding' / 'int8')

_shutdown_event = asyncio.Event()
_ready = False


# ──────────────────────────────────────────────────────────────────────────────
# Lifespan
# ──────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ready

    configure_structlog(service_name='retrieval-api')
    configure_tracer(settings.otel_service_name or "retrieval-api", settings.jaeger_endpoint)
    logger.info("Retrieval API starting up")

    # ── 1. Database pool ────────────────────────────────────────────────────
    db_pool = await create_pool(
        dsn=settings.database_url,
        min_size=5,
        max_size=20,
    )
    app.state.db_pool = db_pool

    # ── 2. Redis ────────────────────────────────────────────────────────────
    redis = await create_redis_client(
        url=settings.redis_url,
        decode_responses=True,
    )
    app.state.redis = redis

    # ── 3. Load ONNX models ─────────────────────────────────────────────────

    # 3a. Bi-encoder (required)
    biencoder_onnx = MODEL_BASE / 'embedding' / 'int8' / 'model.onnx'
    if not biencoder_onnx.exists():
        logger.warning(
            f"Bi-encoder model not found at {biencoder_onnx}. "
            "Query embedding will fail."
        )
        app.state.biencoder_pool = None
        app.state.biencoder_tokenizer = None
    else:
        biencoder_pool = ONNXSessionPool.from_env(str(biencoder_onnx))
        _warm_up_pool(biencoder_pool, label='biencoder')
        app.state.biencoder_pool = biencoder_pool
        app.state.biencoder_tokenizer = BertTokenizerFast.from_pretrained(
            TOKENIZER_PATH
        )
        logger.info("Bi-encoder ONNX pool ready")

    # 3b. Cross-encoder reranker (optional)
    reranker = build_reranker(MODEL_BASE, TOKENIZER_PATH)
    app.state.reranker = reranker
    if reranker:
        logger.info("Cross-encoder reranker ready")

    # 3c. NER query preprocessor (optional)
    preprocessor = build_query_preprocessor(MODEL_BASE, TOKENIZER_PATH)
    app.state.query_preprocessor = preprocessor

    # ── 4. Build BM25 index ─────────────────────────────────────────────────
    bm25_mgr = BM25Manager(db_pool)
    await bm25_mgr.build()
    app.state.bm25_manager = bm25_mgr

    # ── 6. S3 client for presigned URLs ──────────────────────────────────
    if settings.s3_endpoint_url:
        app.state.s3_client = S3Client(
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
        )
        logger.info("S3 client initialized")
    else:
        app.state.s3_client = None
        logger.warning("S3_ENDPOINT_URL not set — presigned URLs unavailable")

    # ── 7. Start BM25 refresh background task ──────────────────────────────
    shutdown_event = asyncio.Event()
    _shutdown_event_holder = shutdown_event  # keep reference for shutdown

    bm25_refresh_task = asyncio.create_task(
        bm25_mgr.start_refresh_loop(shutdown_event),
        name="bm25_refresh",
    )
    app.state.bm25_refresh_task = bm25_refresh_task

    _ready = True
    logger.info("Retrieval API ready")

    yield  # ← application running

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("Retrieval API shutting down")
    _ready = False

    shutdown_event.set()
    try:
        await asyncio.wait_for(bm25_refresh_task, timeout=10.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        bm25_refresh_task.cancel()
        await asyncio.gather(bm25_refresh_task, return_exceptions=True)

    await close_redis()
    await close_pool()
    logger.info("Retrieval API stopped")


def _warm_up_pool(pool: ONNXSessionPool, label: str) -> None:
    """Warmup: run a 1-sample dummy inference through every session in the pool."""
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
            logger.warning(f"[{label}] Warmup run failed (non-fatal): {exc}")

    logger.info(f"[{label}] ONNX pool warmed up: {len(pool._sessions)} sessions")


# ──────────────────────────────────────────────────────────────────────────────
# Application
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="RAG Retrieval API",
    description=(
        "Hybrid dense+sparse retrieval with RRF fusion, MMR re-ranking, "
        "and optional BERT cross-encoder reranking."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
)

# CORS — adjust origins as required
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Prometheus metrics
app.mount("/metrics", get_metrics_app())

# Include retrieval routes
app.include_router(retrieve_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1")
app.include_router(stats_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")

# OpenTelemetry FastAPI auto-instrumentation (requires opentelemetry-instrumentation-fastapi)
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor().instrument_app(app)
except ImportError:
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Health endpoint
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/v1/health", tags=["health"], summary="Readiness probe")
async def health():
    """
    Returns 200 once all startup tasks have completed.
    Returns 503 during initialisation or graceful shutdown.
    """
    if _ready:
        bm25_task = getattr(app.state, 'bm25_refresh_task', None)
        return JSONResponse({
            "status": "ok",
            "bm25_ready": app.state.bm25_manager._index_size > 0
            if hasattr(app.state, 'bm25_manager') else False,
            "biencoder_ready": app.state.biencoder_pool is not None
            if hasattr(app.state, 'biencoder_pool') else False,
        })
    return JSONResponse({"status": "starting"}, status_code=503)
