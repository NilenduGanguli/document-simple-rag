"""
main.py — Ingest API FastAPI application entry point.

Handles startup/shutdown lifecycle, middleware registration, and router mounting.
"""
import hashlib
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from rag_shared.config import get_settings
from rag_shared.logging import configure_structlog, get_logger
from rag_shared.tracing import configure_tracer
from rag_shared.metrics import get_metrics_app
from rag_shared.db.pool import create_pool, close_pool
from rag_shared.cache.redis_client import create_redis_client, close_redis
from rag_shared.queue.connection import get_rabbit_connection, get_channel
from rag_shared.queue.topology import declare_topology

from .routers import documents

logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_structlog(settings.otel_service_name)
    configure_tracer(settings.otel_service_name, settings.jaeger_endpoint)

    # Database pool
    app.state.db_pool = await create_pool(settings.database_url)

    # Redis client
    app.state.redis = await create_redis_client(settings.redis_url)

    # RabbitMQ connection, channel, and topology
    app.state.rabbit_connection = await get_rabbit_connection(settings.rabbitmq_url)
    app.state.rabbit_channel = await get_channel(app.state.rabbit_connection)
    await declare_topology(app.state.rabbit_channel)

    logger.info("Ingest API started")
    yield

    # Cleanup
    await close_redis()
    await close_pool()
    if not app.state.rabbit_connection.is_closed:
        await app.state.rabbit_connection.close()
    logger.info("Ingest API shutdown complete")


app = FastAPI(
    title="RAG Ingest API",
    version="1.0.0",
    description="Document ingestion service for the Enterprise RAG Pipeline",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Prometheus metrics
metrics_app = get_metrics_app()
app.mount("/metrics", metrics_app)

# Include routers
app.include_router(documents.router, prefix="/api/v1")


@app.get("/api/v1/health")
async def health(request: Request):
    status_dict = {"status": "healthy", "service": "ingest-api"}

    try:
        await request.app.state.db_pool.fetchval("SELECT 1")
        status_dict["postgres"] = "ok"
    except Exception as e:
        status_dict["postgres"] = f"error: {e}"
        status_dict["status"] = "degraded"

    try:
        await request.app.state.redis.ping()
        status_dict["redis"] = "ok"
    except Exception as e:
        status_dict["redis"] = f"error: {e}"
        status_dict["status"] = "degraded"

    return status_dict
