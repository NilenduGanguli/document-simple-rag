"""
BM25Manager — maintains an in-memory BM25Okapi index over all embedded chunks.

Adapted from services/retrieval-api/app/bm25_manager.py.
Redis pub/sub listener removed — BM25 is refreshed directly after embedding.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List

from rank_bm25 import BM25Okapi

from rag_shared.config import get_settings
from rag_shared.db.repositories.chunk_repo import ChunkRepository

logger = logging.getLogger(__name__)
settings = get_settings()


class BM25Manager:
    """
    Thread-safe (asyncio-safe) wrapper around a BM25Okapi index.
    Atomic swap on refresh so in-flight searches never see a partial index.
    """

    def __init__(self, db_pool) -> None:
        self._db_pool = db_pool
        self._chunk_repo = ChunkRepository(db_pool)

        self._index: BM25Okapi | None = None
        self._chunk_ids: List[str] = []
        self._chunk_meta: Dict[str, dict] = {}
        self._index_size: int = 0

        self._last_refresh_at: float = 0.0
        self._refresh_interval = settings.bm25_refresh_interval_seconds

    async def build(self) -> None:
        """Build the initial BM25 index at startup."""
        logger.info("Building BM25 index from database …")
        await self._rebuild_index()
        logger.info(f"BM25 index ready: {self._index_size} chunks indexed")

    def search(self, query: str, k: int = 100) -> List[dict]:
        """BM25 sparse search. Returns up to k results."""
        if self._index is None or self._index_size == 0:
            logger.warning("BM25 search called before index is ready")
            return []

        tokens = query.split()
        if not tokens:
            return []

        scores = self._index.get_scores(tokens)
        scored = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scored[:k]:
            if score <= 0.0:
                break
            if idx < len(self._chunk_ids):
                cid = self._chunk_ids[idx]
                meta = self._chunk_meta.get(cid, {})
                results.append({
                    "chunk_id": cid,
                    "bm25_score": float(score),
                    "chunk_text": meta.get("chunk_text", ""),
                    "parent_document_id": meta.get("parent_document_id", ""),
                    "chunk_index": meta.get("chunk_index", 0),
                    "page_number": meta.get("page_number"),
                    "source_type": meta.get("source_type", "text"),
                })
        return results

    async def start_refresh_loop(self, shutdown_event: asyncio.Event) -> None:
        """Background coroutine that periodically rebuilds the BM25 index."""
        logger.info(f"BM25 refresh loop started (interval={self._refresh_interval}s)")
        while not shutdown_event.is_set():
            sleep_remaining = self._refresh_interval
            while sleep_remaining > 0 and not shutdown_event.is_set():
                await asyncio.sleep(min(sleep_remaining, 5.0))
                sleep_remaining -= 5.0

            if shutdown_event.is_set():
                break

            try:
                t0 = time.monotonic()
                await self._rebuild_index()
                elapsed = (time.monotonic() - t0) * 1000
                logger.info(f"BM25 index refreshed: {self._index_size} chunks ({elapsed:.0f}ms)")
            except Exception as exc:
                logger.error(f"BM25 refresh failed: {exc}", exc_info=True)

    async def rebuild_now(self) -> None:
        """Trigger an immediate BM25 rebuild. Called after embedding completes."""
        try:
            t0 = time.monotonic()
            await self._rebuild_index()
            elapsed = (time.monotonic() - t0) * 1000
            logger.info(f"BM25 index rebuilt on-demand: {self._index_size} chunks ({elapsed:.0f}ms)")
        except Exception as exc:
            logger.error(f"BM25 on-demand rebuild failed: {exc}", exc_info=True)

    async def _rebuild_index(self) -> None:
        """Fetch all embedded chunks and atomically replace the BM25 index."""
        rows = await self._chunk_repo.fetch_all_for_bm25()
        if not rows:
            logger.warning("fetch_all_for_bm25 returned 0 rows — skipping rebuild")
            return

        chunk_ids = [r['chunk_id'] for r in rows]
        corpus = [r['chunk_text'].split() for r in rows]

        new_meta = {
            r['chunk_id']: {
                'chunk_text': r.get('chunk_text', ''),
                'parent_document_id': r.get('parent_document_id', ''),
                'chunk_index': r.get('chunk_index', 0),
                'page_number': r.get('page_number'),
                'source_type': r.get('source_type', 'text'),
            }
            for r in rows
        }

        new_index = BM25Okapi(corpus)

        # Atomic swap
        self._index = new_index
        self._chunk_ids = chunk_ids
        self._chunk_meta = new_meta
        self._index_size = len(chunk_ids)
        self._last_refresh_at = time.time()
