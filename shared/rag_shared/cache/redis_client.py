import redis.asyncio as aioredis
from typing import Optional
import logging

logger = logging.getLogger(__name__)
_redis_client: Optional[aioredis.Redis] = None


async def create_redis_client(
    url: str,
    decode_responses: bool = True,
) -> aioredis.Redis:
    global _redis_client
    _redis_client = aioredis.from_url(
        url,
        encoding="utf-8",
        decode_responses=decode_responses,
        max_connections=20,
    )
    # Test connection
    await _redis_client.ping()
    logger.info(f"Redis client connected: {url}")
    return _redis_client


async def get_redis() -> aioredis.Redis:
    if _redis_client is None:
        raise RuntimeError("Redis client not initialized.")
    return _redis_client


async def close_redis():
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
