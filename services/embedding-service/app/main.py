"""
Embedding Service — main entry point.

Responsibilities:
  - Verify ONNX model integrity on startup
  - Build and warm up the ONNXSessionPool
  - Launch EmbeddingWorker as an asyncio background task
  - Serve /health/live, /health/ready, /health/started and /metrics on :8080
    so Kubernetes probes and Prometheus can reach them without coupling to the
    worker process lifecycle.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from rag_shared.config import get_settings
from rag_shared.db.pool import create_pool, close_pool
from rag_shared.cache.redis_client import create_redis_client, close_redis
from rag_shared.queue.connection import get_rabbit_connection
from rag_shared.onnx.session_pool import ONNXSessionPool
from rag_shared.logging.setup import configure_structlog
from rag_shared.metrics import get_metrics_app
from rag_shared.tracing.otel import configure_tracer

from app.startup import verify_model_integrity, warm_up_onnx_pool
from app.worker import EmbeddingWorker

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Module-level state shared between lifespan and health endpoints
# ---------------------------------------------------------------------------
_state: dict = {
    "ready": False,
    "started": False,
    "worker_task": None,
    "shutdown_event": None,
}

MODEL_BASE = Path(os.getenv('MODEL_DEST', '/models'))
# Tokenizer files are stored alongside the embedding INT8 model by model-init
TOKENIZER_PATH = str(MODEL_BASE / 'embedding' / 'int8')
ONNX_MODEL_PATH = str(MODEL_BASE / 'embedding' / 'int8' / 'model.onnx')


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_structlog(service_name='embedding-service')
    configure_tracer(
        settings.otel_service_name or "embedding-service",
        settings.jaeger_endpoint,
    )
    logger.info("Embedding service starting up")

    # 1. Verify model integrity (raises RuntimeError if model is not ready)
    try:
        onnx_path = verify_model_integrity()
    except RuntimeError as exc:
        logger.critical(f"Model integrity check failed: {exc}")
        # Still allow the HTTP server to start so Kubernetes can see the
        # /health/started probe failing rather than a crash loop.
        _state["started"] = True
        yield
        return

    logger.info(f"ONNX model path: {onnx_path}")

    # 2. Build ONNX session pool
    session_pool = ONNXSessionPool.from_env(onnx_path)

    # 3. Warm up sessions (blocks briefly, eliminates first-request jitter)
    warm_up_onnx_pool(session_pool)

    # 4. Connect to infrastructure
    db_pool = await create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=5,
    )
    redis = await create_redis_client(
        url=settings.redis_url,
        decode_responses=False,  # EmbeddingCache stores raw bytes
    )
    rabbit_connection = await get_rabbit_connection(settings.rabbitmq_url)

    # 5. Create and launch EmbeddingWorker
    shutdown_event = asyncio.Event()
    _state["shutdown_event"] = shutdown_event

    worker = EmbeddingWorker(
        db_pool=db_pool,
        redis=redis,
        rabbit_connection=rabbit_connection,
        session_pool=session_pool,
        tokenizer_path=TOKENIZER_PATH,
    )

    worker_task = asyncio.create_task(
        worker.run(shutdown_event), name="embedding_worker"
    )
    _state["worker_task"] = worker_task
    _state["ready"] = True
    _state["started"] = True

    logger.info("Embedding service ready — worker consuming from RabbitMQ")

    yield  # ← application runs here

    # -----------------------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------------------
    logger.info("Embedding service shutting down")
    _state["ready"] = False

    # Signal the worker to stop and wait for it to drain
    shutdown_event.set()
    try:
        await asyncio.wait_for(worker_task, timeout=30.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        logger.warning("Worker task did not finish within 30s — cancelling")
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)

    # Close infrastructure connections
    await rabbit_connection.close()
    await close_redis()
    await close_pool()
    logger.info("Embedding service stopped")


# ---------------------------------------------------------------------------
# FastAPI application (serves probes + metrics on port 8080)
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RAG Embedding Service",
    description="Consumes chunk embedding tasks from RabbitMQ and persists to PGVector",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

# Mount Prometheus metrics at /metrics
app.mount("/metrics", get_metrics_app())

# OpenTelemetry FastAPI auto-instrumentation
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor().instrument_app(app)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Health endpoints (Kubernetes probes)
# ---------------------------------------------------------------------------

@app.get("/health/live", tags=["health"], summary="Liveness probe")
async def liveness():
    """
    Always returns 200 while the process is alive.
    Kubernetes restarts the pod if this fails.
    """
    return JSONResponse({"status": "alive"})


@app.get("/health/started", tags=["health"], summary="Startup probe")
async def startup_probe():
    """
    Returns 200 once the lifespan has finished initialisation.
    Kubernetes waits for this before enabling other probes.
    """
    if _state["started"]:
        return JSONResponse({"status": "started"})
    return JSONResponse({"status": "starting"}, status_code=503)


@app.get("/health/ready", tags=["health"], summary="Readiness probe")
async def readiness():
    """
    Returns 200 when the worker is actively consuming from RabbitMQ.
    Returns 503 during startup, model loading failures, or graceful shutdown.
    """
    worker_task: asyncio.Task = _state.get("worker_task")
    if (
        _state["ready"]
        and worker_task is not None
        and not worker_task.done()
    ):
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "not ready"}, status_code=503)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the metrics/health HTTP server via uvicorn on port 8080."""
    log_level = os.getenv("LOG_LEVEL", "info").lower()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8080,
        log_level=log_level,
        access_log=False,
        # Single worker — the worker uses asyncio internally
        workers=1,
    )


if __name__ == "__main__":
    main()
