"""
OCR Service — RabbitMQ consumer that dispatches OCR to the external ocr-api.

Workflow per message
--------------------
1. Consume OCRTask from ocr_queue (msgpack-encoded).
2. Check Redis cache:  key  ocr:img:{sha256_of_image}  TTL 30 days.
   - Cache hit  → return cached (text, confidence) immediately.
   - Cache miss → forward image to ocr-api via httpx, store result.
3. Publish OCRResult to the reply_to queue specified by the caller with the
   matching correlation_id so the ingestion-worker RPC future resolves.
4. Handle all failures gracefully: always publish an OCRResult even on error
   (success=False, error=<message>) so the caller is never left waiting for
   a reply that will never come.

Concurrency
-----------
OCR_CONCURRENCY (env, default 3) consumer coroutines are started, each with
its own aio_pika channel and prefetch_count=1.
"""

import asyncio
import json
import logging
import os
import signal
import uuid

import aio_pika
import msgpack
import prometheus_client

from opentelemetry import context as otel_context, trace

from rag_shared.cache.redis_client import create_redis_client, close_redis
from rag_shared.config import get_settings
from rag_shared.logging import configure_structlog
from rag_shared.tracing.otel import configure_tracer, extract_trace_context
from rag_shared.queue.connection import get_rabbit_connection
from rag_shared.queue.topology import (
    declare_topology,
    QUEUE_OCR,
)

from .processor import OCRProcessor

settings = get_settings()
logger = logging.getLogger(__name__)

OCR_CONCURRENCY: int = int(os.getenv("OCR_CONCURRENCY", "3"))
# 30 days in seconds
OCR_CACHE_TTL: int = 30 * 24 * 60 * 60


class OCRWorker:
    """
    Manages a pool of consumer coroutines that pull OCR tasks from RabbitMQ,
    optionally resolve cached results from Redis, and publish replies.

    Parameters
    ----------
    redis:              redis.asyncio client (decode_responses=True)
    rabbit_connection:  aio_pika RobustConnection
    concurrency:        number of parallel consumer coroutines
    """

    def __init__(self, redis, rabbit_connection, concurrency: int = OCR_CONCURRENCY):
        self.redis = redis
        self.rabbit_connection = rabbit_connection
        self.concurrency = concurrency
        self.processor = OCRProcessor()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Start all consumer coroutines, then wait for shutdown."""
        tasks = [
            asyncio.create_task(self._consumer_coroutine(i))
            for i in range(self.concurrency)
        ]
        logger.info(f"OCRWorker started — {self.concurrency} consumer coroutines")

        await shutdown_event.wait()

        logger.info("OCRWorker: shutdown requested — cancelling consumers")
        for task in tasks:
            task.cancel()

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.error(f"OCR consumer-{i} raised unexpected error: {result}")

        logger.info("OCRWorker: all consumer coroutines stopped")

    # ------------------------------------------------------------------
    # Consumer coroutine
    # ------------------------------------------------------------------

    async def _consumer_coroutine(self, consumer_id: int) -> None:
        """One dedicated consumer that processes OCR tasks one at a time."""
        channel = await self.rabbit_connection.channel()
        await channel.set_qos(prefetch_count=1)

        queue = await channel.get_queue(QUEUE_OCR)
        logger.info(f"OCR consumer-{consumer_id} ready, listening on {QUEUE_OCR}")

        try:
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    await self._handle_message(message, channel)
        except asyncio.CancelledError:
            logger.info(f"OCR consumer-{consumer_id} cancelled cleanly")
            raise
        except Exception as exc:
            logger.exception(f"OCR consumer-{consumer_id} crashed: {exc}")
            raise
        finally:
            await channel.close()

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    async def _handle_message(
        self,
        message: aio_pika.IncomingMessage,
        channel: aio_pika.Channel,
    ) -> None:
        """
        Process a single OCR task message.

        The aio_pika message.process() context manager ACKs the message on
        clean exit.  All error paths publish an OCRResult with success=False
        rather than raising so the message is always ACKed (preventing
        poison-message redelivery loops).
        """
        async with message.process(requeue=False):
            # Extract trace context from upstream (ingestion-worker)
            ctx = extract_trace_context(dict(message.headers) if message.headers else None)
            token = otel_context.attach(ctx) if ctx else None
            tracer = trace.get_tracer("ocr-service")

            try:
                # Initialise reply metadata to safe defaults so the finally-style
                # reply block has something to send even if unpacking fails.
                correlation_id: str = message.correlation_id or str(uuid.uuid4())
                reply_to: str = message.reply_to or ""
                page_number: int = 0
                result_text: str = ""
                result_confidence: float = 0.0
                success: bool = False
                error_msg: str = ""

                try:
                    payload = msgpack.unpackb(message.body, raw=False)

                    doc_id: str = payload.get("parent_document_id", "unknown")
                    page_number = int(payload.get("page_number", 0))
                    image_bytes: bytes = payload.get("image_bytes", b"")
                    correlation_id = payload.get("reply_correlation_id", correlation_id)

                    if not image_bytes:
                        raise ValueError("OCR task contained empty image_bytes")

                    with tracer.start_as_current_span("ocr.process_page", attributes={
                        "document.id": doc_id,
                        "page.number": page_number,
                        "image.size": len(image_bytes),
                    }):
                        logger.debug(
                            f"OCR task received: doc={doc_id} page={page_number} "
                            f"size={len(image_bytes)} bytes corr={correlation_id}"
                        )

                        # ---- Redis cache lookup ----------------------------------------
                        image_hash = OCRProcessor.compute_image_hash(image_bytes)
                        cache_key = f"ocr:img:{image_hash}"

                        cached_raw = await self.redis.get(cache_key)
                        if cached_raw:
                            cached = json.loads(cached_raw)
                            result_text = cached.get("text", "")
                            result_confidence = float(cached.get("confidence", 0.0))
                            success = True
                            logger.debug(
                                f"OCR cache hit: page={page_number} doc={doc_id} "
                                f"key={cache_key}"
                            )
                        else:
                            # ---- Run OCR (via ocr-api) ------
                            result_text, result_confidence = await self.processor.process(image_bytes)
                            success = True

                            # Store in Redis cache (JSON, decode_responses=True compatible)
                            cache_value = json.dumps(
                                {"text": result_text, "confidence": result_confidence}
                            )
                            await self.redis.set(cache_key, cache_value, ex=OCR_CACHE_TTL)
                            logger.debug(
                                f"OCR cache miss: page={page_number} doc={doc_id} "
                                f"cached with TTL={OCR_CACHE_TTL}s"
                            )

                except Exception as exc:
                    error_msg = str(exc)
                    logger.error(
                        f"OCR processing failed — page={page_number} "
                        f"corr={correlation_id}: {exc}",
                        exc_info=True,
                    )

                # ---- Always publish a reply to unblock the awaiting caller --------
                if reply_to:
                    await self._publish_reply(
                        channel=channel,
                        reply_to=reply_to,
                        correlation_id=correlation_id,
                        page_number=page_number,
                        text=result_text,
                        confidence=result_confidence,
                        success=success,
                        error=error_msg if not success else None,
                    )
                else:
                    logger.warning(
                        f"OCR message has no reply_to queue — "
                        f"result discarded corr={correlation_id}"
                    )
            finally:
                if token is not None:
                    otel_context.detach(token)

    # ------------------------------------------------------------------
    # Reply publisher
    # ------------------------------------------------------------------

    @staticmethod
    async def _publish_reply(
        channel: aio_pika.Channel,
        reply_to: str,
        correlation_id: str,
        page_number: int,
        text: str,
        confidence: float,
        success: bool,
        error: str | None,
    ) -> None:
        """
        Publish an OCRResult msgpack payload to the caller's callback queue.
        Uses the default (nameless) exchange with the reply queue name as the
        routing key — standard RabbitMQ direct-reply pattern.
        """
        reply_body = msgpack.packb(
            {
                "correlation_id": correlation_id,
                "page_number": page_number,
                "text": text,
                "confidence": confidence,
                "success": success,
                "error": error,
            },
            use_bin_type=True,
        )

        await channel.default_exchange.publish(
            aio_pika.Message(
                body=reply_body,
                content_type="application/x-msgpack",
                correlation_id=correlation_id,
                delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
            ),
            routing_key=reply_to,
        )
        logger.debug(
            f"OCR reply published: page={page_number} success={success} "
            f"corr={correlation_id} queue={reply_to}"
        )


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

async def main() -> None:
    configure_structlog(settings.otel_service_name or "ocr-service")
    configure_tracer(
        settings.otel_service_name or "ocr-service",
        settings.jaeger_endpoint,
    )

    # Auto-instrument httpx so trace context propagates to ocr-api
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
        logger.info("httpx auto-instrumentation enabled")
    except ImportError:
        logger.warning("opentelemetry-instrumentation-httpx not available — "
                        "traces will not propagate to ocr-api")

    # Expose Prometheus metrics on port 8082
    try:
        prometheus_client.start_http_server(8082)
        logger.info("Prometheus metrics server started on port 8082")
    except Exception as e:
        logger.warning(f"Failed to start Prometheus metrics server: {e}. Metrics unavailable.")

    # Redis client with decode_responses=True so cache values are strings
    # (JSON-serialised OCR results).
    redis_client = await create_redis_client(settings.redis_url, decode_responses=True)

    connection = await get_rabbit_connection(settings.rabbitmq_url)

    # Ensure all exchanges, queues, and bindings exist before consuming.
    setup_channel = await connection.channel()
    await declare_topology(setup_channel)
    await setup_channel.close()

    worker = OCRWorker(
        redis=redis_client,
        rabbit_connection=connection,
        concurrency=OCR_CONCURRENCY,
    )

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: shutdown_event.set())

    logger.info(f"OCR service starting — concurrency={OCR_CONCURRENCY}")

    try:
        await worker.run(shutdown_event)
    finally:
        await close_redis()
        await connection.close()
        logger.info("OCR service shut down cleanly")


if __name__ == "__main__":
    asyncio.run(main())
