"""
Ingestion worker — consumes tasks from asyncio.Queue (replaces RabbitMQ consumer).

Pipeline per task:
  1. Check hold flag
  2. Download PDF from S3
  3. Route pages (text extraction / OCR)
  4. Perform OCR on image pages via OpenAI Vision
  5. Clean text via TextPreprocessor
  6. Chunk via ChunkingEngine
  7. Bulk-insert chunks into PostgreSQL
  8. Embed chunks via ONNX + upsert to pgvector
  9. Trigger BM25 refresh
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict

from rag_shared.db.repositories.document_repo import DocumentRepository
from rag_shared.db.repositories.chunk_repo import ChunkRepository

from app.pipeline.ocr import perform_ocr_image
from app.pipeline.embedding import embed_and_store_chunks
from app.ingestion.router import IngestionRouter
from app.ingestion.preprocessor import TextPreprocessor
from app.ingestion.chunking.engine import ChunkingEngine

logger = logging.getLogger(__name__)

MAX_RETRY_COUNT = int(os.getenv('MAX_RETRY_COUNT', '3'))
WORKER_CONCURRENCY = int(os.getenv('WORKER_CONCURRENCY', '4'))
OCR_CONCURRENCY = int(os.getenv('OCR_CONCURRENCY_LIMIT', '2'))


class IngestionWorkerPool:
    """
    Manages a pool of asyncio worker coroutines that drain the ingestion queue.
    """

    def __init__(self, app_state) -> None:
        self._state = app_state
        self._ingestion_router = IngestionRouter()
        self._preprocessor = TextPreprocessor()
        self._chunking_engine = ChunkingEngine()
        self._ocr_semaphore = asyncio.Semaphore(OCR_CONCURRENCY)

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Start worker coroutines, wait for shutdown signal."""
        tasks = [
            asyncio.create_task(self._worker_loop(i, shutdown_event))
            for i in range(WORKER_CONCURRENCY)
        ]
        logger.info(f"IngestionWorkerPool started — {WORKER_CONCURRENCY} workers")
        await shutdown_event.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("IngestionWorkerPool stopped")

    async def _worker_loop(self, worker_id: int, shutdown_event: asyncio.Event) -> None:
        """One worker drains the queue until shutdown."""
        logger.info(f"Worker-{worker_id} started")
        while not shutdown_event.is_set():
            try:
                task: Dict[str, Any] = await asyncio.wait_for(
                    self._state.ingestion_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._process_task(task)
            except Exception as exc:
                logger.error(f"Worker-{worker_id}: unhandled error processing task: {exc}", exc_info=True)
            finally:
                self._state.ingestion_queue.task_done()

    async def _process_task(self, payload: Dict[str, Any]) -> None:
        """Process a single ingestion task dict end-to-end."""
        doc_id = payload['parent_document_id']
        force_ocr = bool(payload.get('force_ocr', False))
        chunk_max_tokens = payload.get('chunk_max_tokens')
        chunk_overlap_tokens = payload.get('chunk_overlap_tokens')
        chunking_strategy = payload.get('chunking_strategy')

        doc_repo = DocumentRepository(self._state.db_pool)
        chunk_repo = ChunkRepository(self._state.db_pool)

        try:
            # ── Check hold flag ────────────────────────────────────────────
            if self._state.is_on_hold(doc_id):
                logger.info(f"Doc {doc_id}: on hold — skipping processing")
                await doc_repo.update_status(doc_id, 'on_hold', error_message='Placed on hold by admin')
                return

            if not await doc_repo.update_status(doc_id, 'ingesting'):
                logger.info(f"Doc {doc_id}: on hold (DB guard) — aborting")
                return

            # ── 1. Download PDF ────────────────────────────────────────────
            pdf_bytes = await self._state.s3_client.download_file(
                payload['s3_bucket'], payload['s3_key']
            )

            # ── 2. Route pages ─────────────────────────────────────────────
            routing_result = await self._ingestion_router.route(
                pdf_bytes, doc_id, force_ocr=force_ocr
            )
            await doc_repo.update_metadata(
                doc_id,
                page_count=routing_result.page_count,
                has_text=routing_result.has_text,
                has_images=routing_result.has_images,
            )

            # Check hold after routing (long-running stage)
            if self._state.is_on_hold(doc_id):
                logger.info(f"Doc {doc_id}: on hold after routing — aborting")
                await doc_repo.update_status(doc_id, 'on_hold', error_message='Placed on hold by admin')
                return

            # ── 3. OCR for image pages ─────────────────────────────────────
            if routing_result.images:
                ocr_results = await asyncio.gather(
                    *[self._ocr_image(img, doc_id) for img in routing_result.images],
                    return_exceptions=True,
                )

                # Build OCRResult-like objects for merge_ocr
                class _OCRResult:
                    def __init__(self, page_number, text, success):
                        self.page_number = page_number
                        self.text = text
                        self.success = success

                valid_ocr = []
                for img, result in zip(routing_result.images, ocr_results):
                    if isinstance(result, tuple):
                        text, _ = result
                        valid_ocr.append(_OCRResult(img.page_number, text, bool(text)))
                    else:
                        logger.warning(f"Doc {doc_id}: OCR error for page {img.page_number}: {result}")

                routing_result.merge_ocr(valid_ocr)
            else:
                routing_result.build_full_text()

            # ── 4. Clean text ──────────────────────────────────────────────
            clean_text = self._preprocessor.clean(routing_result.full_text)

            # ── 5. Chunk ───────────────────────────────────────────────────
            loop = asyncio.get_event_loop()
            raw_chunks = await loop.run_in_executor(
                None,
                lambda: self._chunking_engine.chunk(
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
                await doc_repo.update_status(doc_id, 'ready')
                return

            # ── 6. Persist chunks ──────────────────────────────────────────
            chunks_for_db = [
                {
                    **chunk,
                    'chunk_id': str(uuid.uuid4()),
                    'parent_document_id': doc_id,
                    'word_count': len(chunk['chunk_text'].split()),
                }
                for chunk in raw_chunks
            ]

            if self._state.is_on_hold(doc_id):
                logger.info(f"Doc {doc_id}: on hold after chunking — aborting")
                await doc_repo.update_status(doc_id, 'on_hold', error_message='Placed on hold by admin')
                return

            await doc_repo.update_status(doc_id, 'chunking')
            chunk_ids = await chunk_repo.bulk_insert(chunks_for_db)

            if self._state.is_on_hold(doc_id):
                logger.info(f"Doc {doc_id}: on hold before embedding — aborting")
                await doc_repo.update_status(doc_id, 'on_hold', error_message='Placed on hold by admin')
                return

            # ── 7. Embed chunks ────────────────────────────────────────────
            await embed_and_store_chunks(
                chunk_ids=[c['chunk_id'] for c in chunks_for_db],
                doc_id=doc_id,
                app_state=self._state,
            )

            logger.info(f"Doc {doc_id}: ingestion complete ({len(chunk_ids)} chunks)")

        except Exception as exc:
            logger.error(f"Doc {doc_id}: ingestion failed: {exc}", exc_info=True)

            # Retry logic
            retry_count = payload.get('retry_count', 0)
            if retry_count < MAX_RETRY_COUNT:
                logger.info(f"Doc {doc_id}: scheduling retry {retry_count + 1}/{MAX_RETRY_COUNT}")
                retry_payload = {**payload, 'retry_count': retry_count + 1}
                # Exponential backoff via sleep then re-queue
                await asyncio.sleep(30 * (retry_count + 1))
                try:
                    await self._state.ingestion_queue.put(retry_payload)
                except asyncio.QueueFull:
                    logger.error(f"Doc {doc_id}: retry queue full — cannot retry")
                    await doc_repo.update_status(doc_id, 'failed', error_message=str(exc))
            else:
                await doc_repo.update_status(doc_id, 'failed', error_message=str(exc))

    async def _ocr_image(self, img, doc_id: str):
        """OCR a single image with concurrency limiting."""
        async with self._ocr_semaphore:
            return await perform_ocr_image(img.image_bytes, "image/png")
