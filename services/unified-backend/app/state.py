"""
AppState — in-memory state replacing Redis + RabbitMQ for the unified backend.

Provides:
  - dedup_cache:       TTLCache[sha256 -> doc_id]   (7-day TTL)
  - result_cache:      TTLCache[cache_key -> json]  (5-min TTL)
  - hold_flags:        TTLCache[doc_id -> "1"]      (24-hour TTL)
  - ingestion_queue:   asyncio.Queue  (replaces RabbitMQ)
  - rate_limiter:      InMemoryRateLimiter
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

from cachetools import TTLCache

# TTL constants
_DEDUP_TTL = 7 * 24 * 3600    # 7 days
_RESULT_TTL = 300               # 5 minutes
_HOLD_TTL = 86400               # 24 hours


class InMemoryRateLimiter:
    """Simple per-key sliding-window rate limiter using an in-memory dict."""

    def __init__(self) -> None:
        # {key: [timestamp, ...]}  — rolling list of request timestamps
        self._windows: Dict[str, list] = {}

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """Return True if the request is allowed, False if limit exceeded."""
        now = time.monotonic()
        cutoff = now - window_seconds
        timestamps = self._windows.get(key, [])
        # Remove expired entries
        timestamps = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= limit:
            self._windows[key] = timestamps
            return False
        timestamps.append(now)
        self._windows[key] = timestamps
        return True

    def enforce(self, key: str, limit: int, window_seconds: int, name: str = "Rate limit") -> None:
        """Raise ValueError if rate limit exceeded."""
        if not self.check(key, limit, window_seconds):
            raise RateLimitExceeded(f"{name} exceeded: {limit} req/{window_seconds}s for key '{key}'")


class RateLimitExceeded(Exception):
    pass


class AppState:
    """
    Central in-memory state shared across the unified FastAPI app.
    Populated during lifespan startup; accessed via request.app.state.
    """

    def __init__(self) -> None:
        # ── Caches ──────────────────────────────────────────────────────────
        self.dedup_cache: TTLCache = TTLCache(maxsize=50_000, ttl=_DEDUP_TTL)
        self.result_cache: TTLCache = TTLCache(maxsize=1_000, ttl=_RESULT_TTL)
        self.hold_flags: TTLCache = TTLCache(maxsize=10_000, ttl=_HOLD_TTL)

        # ── Background task queue (replaces RabbitMQ) ────────────────────────
        self.ingestion_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        # ── Rate limiter ─────────────────────────────────────────────────────
        self.rate_limiter: InMemoryRateLimiter = InMemoryRateLimiter()

        # ── Database / object storage (set during lifespan) ──────────────────
        self.db_pool = None
        self.s3_client = None

        # ── ONNX models (set during lifespan) ────────────────────────────────
        self.biencoder_pool = None
        self.biencoder_tokenizer = None
        self.reranker = None
        self.query_preprocessor = None

        # ── BM25 manager (set during lifespan) ───────────────────────────────
        self.bm25_manager = None

    # ── Hold flag helpers ────────────────────────────────────────────────────

    def set_hold(self, doc_id: str) -> None:
        self.hold_flags[doc_id] = "1"

    def clear_hold(self, doc_id: str) -> None:
        self.hold_flags.pop(doc_id, None)

    def is_on_hold(self, doc_id: str) -> bool:
        return doc_id in self.hold_flags

    # ── Dedup cache helpers ───────────────────────────────────────────────────

    def get_dedup(self, sha256: str) -> Optional[str]:
        return self.dedup_cache.get(sha256)

    def set_dedup(self, sha256: str, doc_id: str) -> None:
        self.dedup_cache[sha256] = doc_id

    def evict_dedup(self, sha256: str) -> None:
        self.dedup_cache.pop(sha256, None)
