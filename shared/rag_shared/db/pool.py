import asyncpg
from typing import Optional
import logging

logger = logging.getLogger(__name__)
_pool: Optional[asyncpg.Pool] = None


async def create_pool(dsn: str, min_size: int = 5, max_size: int = 20) -> asyncpg.Pool:
    global _pool

    async def init_connection(conn):
        # Register pgvector type codec
        await conn.execute("SET search_path TO public")
        await conn.set_type_codec(
            'vector',
            encoder=lambda v: str(v),
            decoder=lambda v: v,
            schema='public',
            format='text',
        )

    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
        init=init_connection,
        command_timeout=60,
    )
    logger.info(f"Database pool created: min={min_size}, max={max_size}")
    return _pool


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call create_pool() first.")
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")
