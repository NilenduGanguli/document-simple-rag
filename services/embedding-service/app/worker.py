"""
EmbeddingWorker — CPU INT8 ONNX embedding pipeline.

Two coroutines connected by an asyncio Queue:
  1. _prefetch_loop: DB fetch + cache check → prefetch_queue
  2. _embed_and_store_loop: tokenize → ONNX inference (threadpool) → ChromaDB upsert

Batch size: EMBEDDING_BATCH_SIZE (default 16, tuned for CPU RAM bandwidth)
"""
import asyncio
import os
import time
import logging
from typing import List, Tuple, Dict, Optional

import aio_pika
import msgpack
import numpy as np
from transformers import BertTokenizerFast

from opentelemetry import context as otel_context, trace

from rag_shared.config import get_settings
from rag_shared.config import REDIS_CHANNEL_BM25_REFRESH
from rag_shared.onnx.session_pool import ONNXSessionPool
from rag_shared.onnx.math_utils import mean_pooling_np, l2_normalize_np
from rag_shared.metrics import (
    onnx_inference_duration_ms,
    onnx_pool_wait_ms,
    embedding_batch_duration_ms,
    cache_hit_ratio,
)
from rag_shared.cache.embedding_cache import EmbeddingCache
from rag_shared.queue.topology import (
    declare_topology,
    QUEUE_EMBEDDING,
)
from rag_shared.db.repositories.chunk_repo import ChunkRepository
from rag_shared.db.repositories.embedding_repo import EmbeddingRepository
from rag_shared.db.repositories.document_repo import DocumentRepository
from rag_shared.tracing.otel import extract_trace_context

logger = logging.getLogger(__name__)
settings = get_settings()

# Tunable constants — override via environment
_BATCH_SIZE = int(os.getenv('EMBEDDING_BATCH_SIZE', str(settings.embedding_batch_size)))
_PREFETCH_QUEUE_SIZE = int(os.getenv('PREFETCH_QUEUE_SIZE', str(settings.prefetch_queue_size)))
# Maximum time to wait for a full batch before processing a partial one (seconds)
_BATCH_COLLECT_TIMEOUT = float(os.getenv('BATCH_COLLECT_TIMEOUT', '0.1'))
_MODEL_NAME = os.getenv('EMBEDDING_MODEL_NAME', 'bert-base-uncased-int8')


class EmbeddingWorker:
    """
    Consumes EmbeddingTask messages from RabbitMQ, embeds chunks with INT8
    ONNX BERT, persists embeddings to ChromaDB, and caches them in Redis.
    """

    def __init__(
        self,
        db_pool,
        redis,
        rabbit_connection: aio_pika.RobustConnection,
        session_pool: ONNXSessionPool,
        tokenizer_path: str,
        chroma_collection=None,
    ) -> None:
        self.db_pool = db_pool
        self.redis = redis
        self.rabbit_connection = rabbit_connection
        self.session_pool = session_pool

        self.tokenizer = BertTokenizerFast.from_pretrained(tokenizer_path)
        logger.info(f"Tokenizer loaded from {tokenizer_path}")

        self.embedding_cache = EmbeddingCache(redis, model_version=settings.model_version)
        self.prefetch_queue: asyncio.Queue = asyncio.Queue(maxsize=_PREFETCH_QUEUE_SIZE)
        self.batch_size = _BATCH_SIZE

        self.chunk_repo = ChunkRepository(db_pool)
        self.embedding_repo = EmbeddingRepository(chroma_collection)
        self.doc_repo = DocumentRepository(db_pool)

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Start both pipeline stages as concurrent asyncio Tasks."""
        logger.info("EmbeddingWorker starting")
        prefetch_task = asyncio.create_task(
            self._prefetch_loop(shutdown_event), name="prefetch_loop"
        )
        embed_task = asyncio.create_task(
            self._embed_and_store_loop(shutdown_event), name="embed_and_store_loop"
        )
        try:
            await asyncio.gather(prefetch_task, embed_task)
        except asyncio.CancelledError:
            logger.info("EmbeddingWorker tasks cancelled — shutting down")
            prefetch_task.cancel()
            embed_task.cancel()
            await asyncio.gather(prefetch_task, embed_task, return_exceptions=True)

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 1: prefetch loop — RabbitMQ consume + cache check + DB fetch
    # ──────────────────────────────────────────────────────────────────────────

    async def _prefetch_loop(self, shutdown_event: asyncio.Event) -> None:
        """
        Consumes RabbitMQ embedding tasks in batches, checks the embedding
        cache, fetches uncached chunk texts from DB, and drops work items into
        prefetch_queue for Stage 2.

        If ALL chunk_ids for a batch are already cached, the messages are
        acknowledged immediately without touching the prefetch_queue.
        """
        channel = await self.rabbit_connection.channel()
        # One batch in-flight from RabbitMQ at a time;
        # the internal prefetch_queue provides the double-buffering.
        await channel.set_qos(prefetch_count=self.batch_size)
        await declare_topology(channel)

        queue = await channel.get_queue(QUEUE_EMBEDDING)

        logger.info(
            f"Prefetch loop consuming from {QUEUE_EMBEDDING}, "
            f"batch_size={self.batch_size}"
        )

        message_buffer: List[aio_pika.IncomingMessage] = []

        async def _flush_buffer(buf: List[aio_pika.IncomingMessage]) -> None:
            if not buf:
                return
            await self._process_prefetch_batch(buf)

        async with queue.iterator() as q_iter:
            async for message in q_iter:
                if shutdown_event.is_set():
                    # Nack remaining messages so RabbitMQ requeues them
                    await message.nack(requeue=True)
                    for m in message_buffer:
                        await m.nack(requeue=True)
                    break

                message_buffer.append(message)

                if len(message_buffer) >= self.batch_size:
                    await _flush_buffer(message_buffer)
                    message_buffer = []
                else:
                    # Wait briefly to accumulate more messages; then flush partial batch
                    try:
                        await asyncio.wait_for(asyncio.sleep(_BATCH_COLLECT_TIMEOUT), timeout=_BATCH_COLLECT_TIMEOUT)
                    except asyncio.TimeoutError:
                        pass
                    # Flush partial batch — new messages couldn't have arrived during sleep
                    if message_buffer:
                        await _flush_buffer(message_buffer)
                        message_buffer = []

        # Drain remaining messages after shutdown signal
        if message_buffer:
            await _flush_buffer(message_buffer)

    async def _process_prefetch_batch(
        self, messages: List[aio_pika.IncomingMessage]
    ) -> None:
        """
        Unpack tasks, check cache, fetch uncached chunks from DB, then push
        (messages, uncached_chunks, cached_embeddings) onto the prefetch_queue.
        """
        # -----------------------------------------------------------------
        # 1. Unpack all EmbeddingTask payloads
        # -----------------------------------------------------------------
        # Extract trace context from the first message to link this batch to
        # the upstream ingestion-worker trace for the dependency graph.
        first_headers = dict(messages[0].headers) if messages and messages[0].headers else None
        ctx = extract_trace_context(first_headers)
        token = otel_context.attach(ctx) if ctx else None

        # Create an explicit span so Jaeger sees the dependency link
        tracer = trace.get_tracer("embedding-service")
        span = tracer.start_span("embedding.process_batch")
        span_ctx = trace.set_span_in_context(span)
        span_token = otel_context.attach(span_ctx)

        all_chunk_ids: List[str] = []
        for msg in messages:
            try:
                task_dict = msgpack.unpackb(msg.body, raw=False)
                # EmbeddingTask may be packed as dict or as positional list
                if isinstance(task_dict, dict):
                    chunk_ids = task_dict.get('chunk_ids', [])
                elif isinstance(task_dict, (list, tuple)):
                    # Positional: [chunk_ids, parent_document_id, batch_index]
                    chunk_ids = task_dict[0] if task_dict else []
                else:
                    logger.warning(f"Unexpected msgpack payload type: {type(task_dict)}")
                    chunk_ids = []
                all_chunk_ids.extend(chunk_ids)
            except Exception as exc:
                logger.error(f"Failed to unpack EmbeddingTask: {exc}")
                await msg.nack(requeue=False)
                messages.remove(msg)

        if not messages or not all_chunk_ids:
            span.end()
            otel_context.detach(span_token)
            if token is not None:
                otel_context.detach(token)
            return

        # -----------------------------------------------------------------
        # 2. Cache lookup
        # -----------------------------------------------------------------
        cached_embeddings, uncached_ids = await self.embedding_cache.get_batch(all_chunk_ids)

        # Update hit-ratio metric
        cache_hit_ratio.labels(cache_type='embedding').set(
            self.embedding_cache.hit_ratio
        )

        # If everything is cached, update DB status then ack
        if not uncached_ids:
            logger.debug(
                f"All {len(all_chunk_ids)} chunk embeddings found in cache — "
                "updating DB status and acking batch"
            )
            await self.chunk_repo.bulk_update_status(all_chunk_ids, 'done')
            # Fetch parent doc IDs to check for readiness
            cached_chunks = await self.chunk_repo.fetch_by_ids(all_chunk_ids)
            parent_ids = list({c['parent_document_id'] for c in cached_chunks})
            for parent_id in parent_ids:
                marked = await self.doc_repo.mark_ready_if_complete(parent_id)
                if marked:
                    logger.info(f"Doc {parent_id} fully embedded from cache — marked ready")
                    try:
                        await self.redis.publish(REDIS_CHANNEL_BM25_REFRESH, parent_id)
                    except Exception as exc:
                        logger.warning(f"Failed to publish BM25 refresh: {exc}")
            for msg in messages:
                await msg.ack()
            span.end()
            otel_context.detach(span_token)
            if token is not None:
                otel_context.detach(token)
            return

        # -----------------------------------------------------------------
        # 3. Fetch uncached chunk texts from DB
        # -----------------------------------------------------------------
        uncached_chunks = await self.chunk_repo.fetch_by_ids(uncached_ids)
        if not uncached_chunks:
            # Chunks may have been deleted; ack and move on
            logger.warning(
                f"fetch_by_ids returned 0 rows for {len(uncached_ids)} ids"
            )
            for msg in messages:
                await msg.ack()
            span.end()
            otel_context.detach(span_token)
            if token is not None:
                otel_context.detach(token)
            return

        # -----------------------------------------------------------------
        # 4. Hand work to Stage 2
        # -----------------------------------------------------------------
        await self.prefetch_queue.put((messages, uncached_chunks, cached_embeddings))
        span.end()
        otel_context.detach(span_token)
        if token is not None:
            otel_context.detach(token)

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 2: embed-and-store loop — tokenize → ONNX → ChromaDB → cache
    # ──────────────────────────────────────────────────────────────────────────

    async def _embed_and_store_loop(self, shutdown_event: asyncio.Event) -> None:
        """
        Dequeues batches from prefetch_queue, runs BERT INT8 ONNX inference,
        mean-pools, L2-normalises, bulk-upserts to ChromaDB, then caches and
        acks RabbitMQ messages.
        """
        loop = asyncio.get_running_loop()
        logger.info("Embed-and-store loop started")

        while not shutdown_event.is_set() or not self.prefetch_queue.empty():
            # -----------------------------------------------------------------
            # Dequeue next work item (with timeout to allow shutdown checks)
            # -----------------------------------------------------------------
            try:
                batch_data = await asyncio.wait_for(
                    self.prefetch_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            messages, uncached_chunks, cached_embeddings = batch_data

            if not uncached_chunks:
                for msg in messages:
                    await msg.ack()
                self.prefetch_queue.task_done()
                continue

            t_batch_start = time.monotonic()

            try:
                await self._embed_batch(loop, messages, uncached_chunks, cached_embeddings)
            except Exception as exc:
                logger.error(f"Embed batch failed: {exc}", exc_info=True)
                # Nack — let dead-letter exchange handle retries
                for msg in messages:
                    try:
                        await msg.nack(requeue=False)
                    except Exception:
                        pass
            finally:
                self.prefetch_queue.task_done()
                batch_ms = (time.monotonic() - t_batch_start) * 1000
                embedding_batch_duration_ms.observe(batch_ms)

    async def _embed_batch(
        self,
        loop: asyncio.AbstractEventLoop,
        messages: List[aio_pika.IncomingMessage],
        uncached_chunks: List[dict],
        cached_embeddings: Dict[str, np.ndarray],
    ) -> None:
        """Core embedding logic: tokenize → ONNX → pool → upsert → cache → ack."""
        # -----------------------------------------------------------------
        # 1. Tokenise
        # -----------------------------------------------------------------
        texts = [chunk['chunk_text'] for chunk in uncached_chunks]
        encoded = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors='np',
        )

        input_ids = encoded['input_ids'].astype(np.int64)
        attention_mask = encoded['attention_mask'].astype(np.int64)
        token_type_ids = encoded.get(
            'token_type_ids', np.zeros_like(input_ids)
        ).astype(np.int64)

        # -----------------------------------------------------------------
        # 2. ONNX inference (run in threadpool to avoid blocking the event loop)
        # -----------------------------------------------------------------
        t_infer = time.monotonic()
        async with self.session_pool.acquire() as (session, wait_ms):
            onnx_pool_wait_ms.labels(model_type='biencoder').observe(wait_ms)

            outputs = await loop.run_in_executor(
                None,
                lambda: session.run(
                    None,
                    {
                        'input_ids': input_ids,
                        'attention_mask': attention_mask,
                        'token_type_ids': token_type_ids,
                    },
                ),
            )

        infer_ms = (time.monotonic() - t_infer) * 1000
        onnx_inference_duration_ms.labels(model_type='biencoder').observe(infer_ms)
        logger.debug(f"ONNX inference: {len(texts)} texts in {infer_ms:.1f}ms")

        # outputs[0] → last_hidden_state [batch, seq_len, 768]
        last_hidden_state: np.ndarray = outputs[0]

        # -----------------------------------------------------------------
        # 3. Mean pooling + L2 normalise → [batch, 768]
        # -----------------------------------------------------------------
        pooled = mean_pooling_np(last_hidden_state, encoded['attention_mask'])
        embeddings = l2_normalize_np(pooled)

        # -----------------------------------------------------------------
        # 4. Bulk upsert to ChromaDB
        # -----------------------------------------------------------------
        chunk_ids = [c['chunk_id'] for c in uncached_chunks]
        parent_doc_ids = [c['parent_document_id'] for c in uncached_chunks]
        embedding_lists = [embeddings[i].tolist() for i in range(len(uncached_chunks))]

        await self.embedding_repo.bulk_upsert(
            chunk_ids=chunk_ids,
            parent_doc_ids=parent_doc_ids,
            embeddings=embedding_lists,
            model_name=_MODEL_NAME,
            model_version=settings.model_version,
        )
        logger.debug(f"Upserted {len(chunk_ids)} embeddings to ChromaDB")

        # -----------------------------------------------------------------
        # 5. Cache new embeddings
        # -----------------------------------------------------------------
        new_emb_dict = {
            chunk_ids[i]: embeddings[i] for i in range(len(chunk_ids))
        }
        await self.embedding_cache.set_batch(new_emb_dict)

        # -----------------------------------------------------------------
        # 6. Update chunk status to 'done'
        # -----------------------------------------------------------------
        await self.chunk_repo.bulk_update_status(chunk_ids, 'done')

        # -----------------------------------------------------------------
        # 7. Mark parent document(s) ready if all chunks are now embedded
        # -----------------------------------------------------------------
        parent_ids = list({c['parent_document_id'] for c in uncached_chunks})
        for parent_id in parent_ids:
            marked = await self.doc_repo.mark_ready_if_complete(parent_id)
            if marked:
                logger.info(f"Doc {parent_id} fully embedded — marked ready")
                try:
                    await self.redis.publish(REDIS_CHANNEL_BM25_REFRESH, parent_id)
                except Exception as exc:
                    logger.warning(f"Failed to publish BM25 refresh: {exc}")

        # -----------------------------------------------------------------
        # 8. Ack all RabbitMQ messages in the batch
        # -----------------------------------------------------------------
        for msg in messages:
            await msg.ack()

        # -----------------------------------------------------------------
        # 8. Refresh cache-hit-ratio metric
        # -----------------------------------------------------------------
        cache_hit_ratio.labels(cache_type='embedding').set(
            self.embedding_cache.hit_ratio
        )

        logger.info(
            f"Embedded batch: chunks={len(chunk_ids)}, "
            f"onnx_ms={infer_ms:.1f}, total_msgs={len(messages)}"
        )
