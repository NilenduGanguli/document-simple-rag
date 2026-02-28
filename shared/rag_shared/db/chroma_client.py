"""ChromaDB async client factory for vector storage."""
import logging
from urllib.parse import urlparse

import chromadb
from chromadb.api import AsyncClientAPI

logger = logging.getLogger(__name__)

_client: AsyncClientAPI | None = None


async def get_chroma_client(url: str) -> AsyncClientAPI:
    """Create or return the singleton async ChromaDB client."""
    global _client
    if _client is None:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 8000
        _client = await chromadb.AsyncHttpClient(host=host, port=port)
        logger.info(f"ChromaDB client connected: {host}:{port}")
    return _client


async def get_embedding_collection(client: AsyncClientAPI):
    """Get or create the chunk_embeddings collection with cosine distance."""
    collection = await client.get_or_create_collection(
        name="chunk_embeddings",
        metadata={"hnsw:space": "cosine"},
    )
    logger.info("ChromaDB collection 'chunk_embeddings' ready")
    return collection


async def close_chroma():
    """Reset the global client reference."""
    global _client
    _client = None
    logger.info("ChromaDB client closed")
