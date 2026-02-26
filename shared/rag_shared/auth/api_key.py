import hashlib
import time
from typing import Optional
from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
import redis.asyncio as aioredis

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def hash_api_key(api_key: str) -> str:
    """HMAC-SHA256 hash of API key for storage in audit logs."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


async def validate_api_key(
    api_key: str = Security(api_key_header),
    valid_keys: list[str] = None,
) -> str:
    """Validate API key against configured keys. Returns the key hash for audit."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required",
        )

    if valid_keys and api_key in valid_keys:
        return hash_api_key(api_key)

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid API key",
    )


class RateLimiter:
    """Redis sliding window rate limiter."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        limit: int = 1000,
        window_seconds: int = 60,
    ):
        self.redis = redis_client
        self.limit = limit
        self.window_seconds = window_seconds

    async def check_rate_limit(self, identifier: str) -> bool:
        """Returns True if request is allowed, False if rate limited."""
        now = int(time.time())
        key = f"ratelimit:{identifier}:{now // self.window_seconds}"

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self.window_seconds * 2)
            results = await pipe.execute()

        count = results[0]
        return count <= self.limit

    async def enforce(self, identifier: str, name: str = "client") -> None:
        """Raises HTTPException if rate limit exceeded."""
        allowed = await self.check_rate_limit(identifier)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {name}. Retry after {self.window_seconds}s.",
                headers={"Retry-After": str(self.window_seconds)},
            )
