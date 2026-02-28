"""
Ingestion Worker main entry point.
Runs WORKER_CONCURRENCY async consumer coroutines.
Each coroutine processes one document at a time (prefetch=1 per consumer).
"""
import asyncio
import signal
import logging
import prometheus_client
from rag_shared.config import get_settings
from rag_shared.logging import configure_structlog
from rag_shared.tracing.otel import configure_tracer
from rag_shared.db.pool import create_pool, close_pool
from rag_shared.cache.redis_client import create_redis_client, close_redis
from rag_shared.queue.connection import get_rabbit_connection, get_channel
from rag_shared.queue.topology import ensure_topology
from rag_shared.storage.s3_client import S3Client
from .worker import IngestionWorker

settings = get_settings()
logger = logging.getLogger(__name__)

async def main():
    configure_structlog(settings.otel_service_name or "ingestion-worker")
    configure_tracer(
        settings.otel_service_name or "ingestion-worker",
        settings.jaeger_endpoint,
    )

    # Expose Prometheus metrics on port 8081
    try:
        prometheus_client.start_http_server(8081)
        logger.info("Prometheus metrics server started on port 8081")
    except Exception as e:
        logger.warning(f"Failed to start Prometheus metrics server: {e}. Metrics unavailable.")

    db_pool = await create_pool(settings.database_url)
    redis = await create_redis_client(settings.redis_url)

    connection = await get_rabbit_connection(settings.rabbitmq_url)

    s3_client = S3Client(
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )

    worker = IngestionWorker(
        db_pool=db_pool,
        redis=redis,
        rabbit_connection=connection,
        s3_client=s3_client,
        concurrency=settings.worker_concurrency,
    )

    # Setup topology
    setup_channel = await get_channel(connection)
    await ensure_topology(setup_channel)
    await setup_channel.close()

    # Handle shutdown signals
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: shutdown_event.set())

    logger.info(f"Starting worker with concurrency={settings.worker_concurrency}")

    try:
        await worker.run(shutdown_event)
    finally:
        await close_redis()
        await close_pool()
        await connection.close()

if __name__ == '__main__':
    asyncio.run(main())
