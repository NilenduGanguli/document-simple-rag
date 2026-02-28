"""
Ingestion Worker — message consumer and document processing orchestrator.

Each pod runs WORKER_CONCURRENCY=6 async consumer coroutines. Every coroutine
owns its own aio_pika channel with prefetch_count=1 so that a slow document
does not starve the other workers on the same pod.

Processing pipeline per message
--------------------------------
1. Download PDF bytes from S3
2. Route through IngestionRouter (PyMuPDF text-density analysis)
3. Dispatch image pages to OCR Service via RabbitMQ RPC (30 s timeout)
4. Merge OCR text with digital text; clean via TextPreprocessor
5. Chunk via ChunkingEngine (RecursiveCharacterSplitter, 512 tokens)
6. Bulk-insert chunks into PostgreSQL
7. Publish chunk IDs to embedding_queue in batches of 16

Retry / DLQ strategy (Section 6.3)
------------------------------------
- On failure, increment retry_count in DB (via DocumentRepository.increment_retry)
- retry_count <= MAX_RETRY_COUNT  -> sleep with exponential back-off then
  re-publish a fresh ingestion task (original message is already ACKed inside
  process(requeue=False) context).
- retry_count >  MAX_RETRY_COUNT  -> update document status='failed' and
  route the payload to the dead-letter exchange directly.
"""

import asyncio
import logging
import os
import uuid
from typing import List

import aio_pika
import msgpack

from rag_shared.db.repositories.document_repo import DocumentRepository
from rag_shared.db.repositories.chunk_repo import ChunkRepository
from rag_shared.queue.schemas import OCRResult, EmbeddingTask
from rag_shared.queue.topology import (
    QUEUE_INGESTION,
    EXCHANGE_INGESTION,
    EXCHANGE_INGESTION_DLX,
    EXCHANGE_EMBEDDING,
    EXCHANGE_OCR,
    RK_INGEST,
    RK_EMBED,
    RK_OCR,
)

from .router import IngestionRouter
from .preprocessor import TextPreprocessor
from .chunking.engine import ChunkingEngine

logger = logging.getLogger(__name__)

MAX_RETRY_COUNT = 3
OCR_TIMEOUT = 30.0           # seconds to wait for an OCR reply
BASE_RETRY_DELAY = 30        # seconds; multiplied by retry count for back-off


class IngestionWorker:
    """
    Orchestrates the ingestion pipeline for a single worker pod.

    Parameters
    ----------
    db_pool:           asyncpg connection pool
    redis:             redis.asyncio client
    rabbit_connection: aio_pika RobustConnection
    s3_client:         rag_shared S3Client
    concurrency:       number of parallel consumer coroutines (default 6)
    """

    def __init__(self, db_pool, redis, rabbit_connection, s3_client, concurrency: int = 6):
        self.db_pool = db_pool
        self.redis = redis
        self.rabbit_connection = rabbit_connection
        self.s3_client = s3_client
        self.concurrency = concurrency

        # Shared, stateless processing components
        self.ingestion_router = IngestionRouter()
        self.preprocessor = TextPreprocessor()
        self.chunking_engine = ChunkingEngine()
        self.doc_repo = DocumentRepository(db_pool)
        self.chunk_repo = ChunkRepository(db_pool)

        # Limit concurrent OCR requests to match the OCR service concurrency (default 3)
        ocr_concurrency = int(os.getenv('OCR_CONCURRENCY_LIMIT', '3'))
        self._ocr_semaphore = asyncio.Semaphore(ocr_concurrency)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """
        Start all consumer coroutines, then wait for shutdown signal.
        Each coroutine owns its own channel so blocking on one message
        does not prevent other coroutines from accepting work.
        """
        tasks = [
            asyncio.create_task(self._consumer_coroutine(i))
            for i in range(self.concurrency)
        ]

        logger.info(f"IngestionWorker started — {self.concurrency} consumer coroutines")
        await shutdown_event.wait()

        logger.info("Shutdown requested — cancelling consumer coroutines")
        for task in tasks:
            task.cancel()

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.error(f"Consumer coroutine {i} raised unexpected error: {result}")

        logger.info("All consumer coroutines stopped")

    # ------------------------------------------------------------------
    # Consumer coroutine
    # ------------------------------------------------------------------

    async def _consumer_coroutine(self, consumer_id: int) -> None:
        """One long-lived coroutine that processes messages from the ingestion queue."""
        channel = await self.rabbit_connection.channel()
        await channel.set_qos(prefetch_count=1)

        # Obtain reference to the already-declared queue (declared during topology setup)
        queue = await channel.get_queue(QUEUE_INGESTION)
        logger.info(f"Consumer-{consumer_id} ready, listening on {QUEUE_INGESTION}")

        try:
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    await self._process_document(message, channel)
        except asyncio.CancelledError:
            logger.info(f"Consumer-{consumer_id} cancelled cleanly")
            raise
        except Exception as exc:
            logger.exception(f"Consumer-{consumer_id} crashed: {exc}")
            raise
        finally:
            await channel.close()

    # ------------------------------------------------------------------
    # Core document processing
    # ------------------------------------------------------------------

    async def _is_on_hold(self, doc_id: str) -> bool:
        """Check Redis for a hold flag set by the admin hold endpoint."""
        try:
            flag = await self.redis.get(f"doc:hold:{doc_id}")
            return flag is not None
        except Exception:
            return False

    async def _process_document(
        self,
        message: aio_pika.IncomingMessage,
        channel: aio_pika.Channel,
    ) -> None:
        """
        Process a single ingestion message end-to-end.
        The aio_pika message.process() context manager ACKs the message
        on clean exit and NACKs (no-requeue) on unhandled exception.
        All retries are handled by re-publishing a fresh message so the
        original is always ACKed inside this context.
        """
        async with message.process(requeue=False):
            payload = msgpack.unpackb(message.body, raw=False)
            doc_id = payload['parent_document_id']

            # Extract per-message chunking overrides (set by reprocess endpoint;
            # absent in normal ingestion messages so defaults apply)
            force_ocr: bool = bool(payload.get('force_ocr', False))
            chunk_max_tokens = payload.get('chunk_max_tokens')    # None → env default
            chunk_overlap_tokens = payload.get('chunk_overlap_tokens')  # None → env default
            chunking_strategy = payload.get('chunking_strategy')   # None → env default

            try:
                # Check hold flag before starting
                if await self._is_on_hold(doc_id):
                    logger.info(f"Doc {doc_id}: on hold — skipping processing")
                    await self.doc_repo.update_status(doc_id, 'on_hold', error_message='Placed on hold by admin')
                    return

                await self.doc_repo.update_status(doc_id, 'ingesting')

                # 1. Download PDF
                pdf_bytes = await self.s3_client.download_file(
                    payload['s3_bucket'], payload['s3_key']
                )

                # 2. Route pages — text extraction or image/OCR
                routing_result = await self.ingestion_router.route(
                    pdf_bytes, doc_id, force_ocr=force_ocr
                )

                # Persist routing metadata to the document record
                await self.doc_repo.update_metadata(
                    doc_id,
                    page_count=routing_result.page_count,
                    has_text=routing_result.has_text,
                    has_images=routing_result.has_images,
                )

                # Check hold flag after routing/OCR (long-running stage)
                if await self._is_on_hold(doc_id):
                    logger.info(f"Doc {doc_id}: on hold after routing — aborting")
                    await self.doc_repo.update_status(doc_id, 'on_hold', error_message='Placed on hold by admin')
                    return

                # 3. Dispatch OCR for image pages concurrently
                if routing_result.images:
                    ocr_results = await asyncio.gather(
                        *[self._dispatch_ocr(img, doc_id, channel) for img in routing_result.images],
                        return_exceptions=True,
                    )
                    # Filter out exceptions / None so merge_ocr receives only valid OCRResult objects
                    valid_ocr = [
                        r for r in ocr_results
                        if isinstance(r, OCRResult)
                    ]
                    if len(valid_ocr) < len(routing_result.images):
                        logger.warning(
                            f"Doc {doc_id}: {len(routing_result.images) - len(valid_ocr)} "
                            "OCR requests failed or timed out"
                        )
                    routing_result.merge_ocr(valid_ocr)
                else:
                    routing_result.build_full_text()

                # 4. Clean text
                clean_text = self.preprocessor.clean(routing_result.full_text)

                # 5. Chunk (run in executor so event loop stays alive for RabbitMQ heartbeats)
                loop = asyncio.get_event_loop()
                raw_chunks = await loop.run_in_executor(
                    None,
                    lambda: self.chunking_engine.chunk(
                        clean_text,
                        doc_id,
                        routing_result,
                        strategy_name=chunking_strategy,
                        max_tokens=chunk_max_tokens,
                        overlap_tokens=chunk_overlap_tokens,
                    ),
                )

                if not raw_chunks:
                    logger.warning(f"Doc {doc_id}: no chunks produced — marking ready")
                    await self.doc_repo.update_status(doc_id, 'ready')
                    return

                # Attach storage-layer fields required by ChunkRepository.bulk_insert
                chunks_for_db = [
                    {
                        **chunk,
                        'chunk_id': str(uuid.uuid4()),
                        'parent_document_id': doc_id,
                        'word_count': len(chunk['chunk_text'].split()),
                    }
                    for chunk in raw_chunks
                ]

                # 6. Persist chunks
                chunk_ids = await self.chunk_repo.bulk_insert(chunks_for_db)

                # Check hold flag before publishing embeddings
                if await self._is_on_hold(doc_id):
                    logger.info(f"Doc {doc_id}: on hold after chunking — aborting before embedding")
                    await self.doc_repo.update_status(doc_id, 'on_hold', error_message='Placed on hold by admin')
                    return

                # 7. Update status and publish to embedding queue
                await self.doc_repo.update_status(doc_id, 'chunking')
                await self._publish_embedding_batch(chunk_ids, doc_id, channel, batch_size=16)

                logger.info(
                    f"Doc {doc_id} ingested successfully — "
                    f"{len(chunk_ids)} chunks queued for embedding"
                )

            except Exception as exc:
                await self._handle_failure(doc_id, exc, payload, channel)

    # ------------------------------------------------------------------
    # OCR RPC dispatch
    # ------------------------------------------------------------------

    async def _dispatch_ocr(self, img_data, doc_id: str, channel: aio_pika.Channel):
        """
        Send an OCRTask to the ocr_queue and wait for the reply via a
        temporary exclusive callback queue.  Returns an OCRResult object,
        or an OCRResult with success=False on timeout / error.

        Pattern: RabbitMQ Direct-Reply-To (manual variant)
        ---------------------------------------------------
        1. Declare a server-named, exclusive, auto-delete callback queue.
        2. Publish OCRTask with reply_to=<callback_queue_name> and correlation_id.
        3. Consume the callback queue and resolve on matching correlation_id.
        4. Cancel consumer + delete queue in the finally block.
        """
        async with self._ocr_semaphore:
            return await self._dispatch_ocr_inner(img_data, doc_id, channel)

    async def _dispatch_ocr_inner(self, img_data, doc_id: str, channel: aio_pika.Channel):
        correlation_id = str(uuid.uuid4())

        # Declare temp callback queue on this channel before publishing so
        # the reply cannot arrive before we start consuming.
        callback_queue = await channel.declare_queue(
            name='',          # broker assigns a unique name
            exclusive=True,
            auto_delete=True,
        )

        result_future: asyncio.Future = asyncio.get_event_loop().create_future()

        async def _on_reply(reply_msg: aio_pika.IncomingMessage) -> None:
            async with reply_msg.process():
                if reply_msg.correlation_id != correlation_id:
                    # Stale reply from a previous request on the same queue — ignore
                    return
                try:
                    data = msgpack.unpackb(reply_msg.body, raw=False)
                    ocr_result = OCRResult(
                        correlation_id=data.get('correlation_id', correlation_id),
                        page_number=data.get('page_number', img_data.page_number),
                        text=data.get('text', ''),
                        confidence=data.get('confidence', 0.0),
                        success=data.get('success', False),
                        error=data.get('error'),
                    )
                except Exception as parse_exc:
                    ocr_result = OCRResult(
                        correlation_id=correlation_id,
                        page_number=img_data.page_number,
                        text='',
                        confidence=0.0,
                        success=False,
                        error=f"Reply parse error: {parse_exc}",
                    )
                if not result_future.done():
                    result_future.set_result(ocr_result)

        consumer_tag = await callback_queue.consume(_on_reply)

        try:
            # Publish the OCR task
            ocr_exchange = await channel.get_exchange(EXCHANGE_OCR)
            task_body = msgpack.packb(
                {
                    'parent_document_id': doc_id,
                    'page_number': img_data.page_number,
                    'image_bytes': img_data.image_bytes,
                    'is_full_page': img_data.is_full_page,
                    'reply_correlation_id': correlation_id,
                },
                use_bin_type=True,
            )
            await ocr_exchange.publish(
                aio_pika.Message(
                    body=task_body,
                    content_type='application/x-msgpack',
                    correlation_id=correlation_id,
                    reply_to=callback_queue.name,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=RK_OCR,
            )

            # Wait for the response
            return await asyncio.wait_for(result_future, timeout=OCR_TIMEOUT)

        except asyncio.TimeoutError:
            logger.error(
                f"OCR timeout ({OCR_TIMEOUT}s) for page {img_data.page_number} of doc {doc_id}"
            )
            return OCRResult(
                correlation_id=correlation_id,
                page_number=img_data.page_number,
                text='',
                confidence=0.0,
                success=False,
                error='ocr_timeout',
            )
        except Exception as exc:
            logger.error(
                f"OCR dispatch error for page {img_data.page_number} of doc {doc_id}: {exc}"
            )
            return OCRResult(
                correlation_id=correlation_id,
                page_number=img_data.page_number,
                text='',
                confidence=0.0,
                success=False,
                error=str(exc),
            )
        finally:
            try:
                await callback_queue.cancel(consumer_tag)
            except Exception:
                pass
            try:
                await callback_queue.delete()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Embedding batch publisher
    # ------------------------------------------------------------------

    async def _publish_embedding_batch(
        self,
        chunk_ids: List[str],
        doc_id: str,
        channel: aio_pika.Channel,
        batch_size: int = 16,
    ) -> None:
        """Publish chunk IDs to the embedding queue as micro-batches of `batch_size`."""
        embedding_exchange = await channel.get_exchange(EXCHANGE_EMBEDDING)

        for batch_index, offset in enumerate(range(0, len(chunk_ids), batch_size)):
            batch = chunk_ids[offset: offset + batch_size]

            task = EmbeddingTask(
                chunk_ids=batch,
                parent_document_id=doc_id,
                batch_index=batch_index,
            )
            body = msgpack.packb(
                {
                    'chunk_ids': task.chunk_ids,
                    'parent_document_id': task.parent_document_id,
                    'batch_index': task.batch_index,
                },
                use_bin_type=True,
            )
            await embedding_exchange.publish(
                aio_pika.Message(
                    body=body,
                    content_type='application/x-msgpack',
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=RK_EMBED,
            )

        logger.debug(
            f"Doc {doc_id}: published {len(chunk_ids)} chunk IDs to embedding queue "
            f"in {-(-len(chunk_ids) // batch_size)} batch(es)"
        )

    # ------------------------------------------------------------------
    # Failure handling / retry / DLQ
    # ------------------------------------------------------------------

    async def _handle_failure(
        self,
        doc_id: str,
        error: Exception,
        payload: dict,
        channel: aio_pika.Channel,
    ) -> None:
        """
        Retry or dead-letter the failed document.

        retry_count is stored in the DB (DocumentRepository.increment_retry)
        so it persists across pod restarts.  For retries <= MAX_RETRY_COUNT
        we sleep with exponential back-off (30 s * retry_count, capped at 120 s)
        then re-publish a fresh ingestion task.  The original message is ACKed
        by the enclosing process() context — no double-delivery risk.
        """
        logger.error(f"Processing failure for doc {doc_id}: {error}", exc_info=True)

        try:
            retry_count = await self.doc_repo.increment_retry(doc_id)
        except Exception as db_err:
            logger.error(f"Could not increment retry for {doc_id}: {db_err}")
            retry_count = MAX_RETRY_COUNT + 1  # force DLQ path

        if retry_count <= MAX_RETRY_COUNT:
            delay = min(BASE_RETRY_DELAY * retry_count, 120)
            logger.warning(
                f"Doc {doc_id}: retry {retry_count}/{MAX_RETRY_COUNT} — "
                f"re-queuing after {delay}s back-off"
            )
            await asyncio.sleep(delay)
            await self.doc_repo.update_status(doc_id, 'pending', error_message=str(error))
            await self._requeue_document(payload, retry_count, channel)
        else:
            logger.error(
                f"Doc {doc_id}: exceeded {MAX_RETRY_COUNT} retries — routing to DLQ"
            )
            await self.doc_repo.update_status(doc_id, 'failed', error_message=str(error))
            await self._publish_to_dlq(payload, str(error), channel)

    async def _requeue_document(
        self,
        payload: dict,
        retry_count: int,
        channel: aio_pika.Channel,
    ) -> None:
        """Re-publish the ingestion task with an incremented retry count header."""
        updated_payload = {**payload, 'retry_count': retry_count}
        body = msgpack.packb(updated_payload, use_bin_type=True)

        ingestion_exchange = await channel.get_exchange(EXCHANGE_INGESTION)
        await ingestion_exchange.publish(
            aio_pika.Message(
                body=body,
                content_type='application/x-msgpack',
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers={'x-retry-count': retry_count},
            ),
            routing_key=RK_INGEST,
        )
        logger.debug(f"Doc {payload.get('parent_document_id')}: re-queued (retry {retry_count})")

    async def _publish_to_dlq(
        self,
        payload: dict,
        error_message: str,
        channel: aio_pika.Channel,
    ) -> None:
        """Route a permanently failed document to the dead-letter exchange."""
        dlq_payload = {**payload, 'error_message': error_message}
        body = msgpack.packb(dlq_payload, use_bin_type=True)

        dlx = await channel.get_exchange(EXCHANGE_INGESTION_DLX)
        await dlx.publish(
            aio_pika.Message(
                body=body,
                content_type='application/x-msgpack',
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers={'x-failure-reason': error_message[:255]},
            ),
            routing_key=RK_INGEST,
        )
        logger.info(f"Doc {payload.get('parent_document_id')}: published to DLQ")
