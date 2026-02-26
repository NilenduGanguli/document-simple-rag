import numpy as np
from typing import Dict, List, Optional, Tuple
import redis.asyncio as aioredis
import logging

logger = logging.getLogger(__name__)

EMBEDDING_TTL = 72 * 3600  # 72 hours


def _encode_embedding(embedding: np.ndarray) -> bytes:
    """Encode float32 numpy array to bytes."""
    return embedding.astype(np.float32).tobytes()


def _decode_embedding(data: bytes) -> np.ndarray:
    """Decode bytes to float32 numpy array."""
    return np.frombuffer(data, dtype=np.float32)


class EmbeddingCache:
    def __init__(self, redis_client: aioredis.Redis, model_version: str = "v1"):
        self.redis = redis_client
        self.model_version = model_version
        self._hits = 0
        self._misses = 0

    def _key(self, chunk_id: str) -> str:
        return f"emb:{self.model_version}:{chunk_id}"

    async def get_batch(
        self,
        chunk_ids: List[str],
    ) -> Tuple[Dict[str, np.ndarray], List[str]]:
        """
        Look up a batch of chunk embeddings by ID.

        Returns:
            cached:   mapping of chunk_id -> float32 ndarray for cache hits
            uncached: list of chunk_ids that were not found in cache
        """
        if not chunk_ids:
            return {}, []

        keys = [self._key(cid) for cid in chunk_ids]
        results = await self.redis.mget(*keys)

        cached: Dict[str, np.ndarray] = {}
        uncached: List[str] = []

        for chunk_id, result in zip(chunk_ids, results):
            if result is not None:
                # Redis returns bytes when decode_responses=False; when
                # decode_responses=True it may return a str – handle both.
                if isinstance(result, str):
                    result = result.encode('latin-1')
                cached[chunk_id] = _decode_embedding(result)
                self._hits += 1
            else:
                uncached.append(chunk_id)
                self._misses += 1

        return cached, uncached

    async def set_batch(self, embeddings: Dict[str, np.ndarray]) -> None:
        """Cache multiple embeddings with TTL."""
        if not embeddings:
            return

        async with self.redis.pipeline(transaction=False) as pipe:
            for chunk_id, embedding in embeddings.items():
                pipe.setex(
                    self._key(chunk_id),
                    EMBEDDING_TTL,
                    _encode_embedding(embedding),
                )
            await pipe.execute()

    @property
    def hit_ratio(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def reset_stats(self) -> None:
        """Reset hit / miss counters (useful between test cases)."""
        self._hits = 0
        self._misses = 0
