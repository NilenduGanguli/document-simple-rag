"""
ChromaDB client — replaced by pgvector (PostgreSQL).

This module is kept as a stub for backward compatibility with legacy
microservices that import from it. The unified backend no longer uses ChromaDB.
"""
import logging

logger = logging.getLogger(__name__)


async def get_chroma_client(url: str):
    raise NotImplementedError(
        "ChromaDB has been replaced by pgvector. "
        "Use the PostgreSQL connection pool (rag_shared.db.pool) instead."
    )


async def get_embedding_collection(client):
    raise NotImplementedError("ChromaDB has been replaced by pgvector.")


async def close_chroma():
    pass
