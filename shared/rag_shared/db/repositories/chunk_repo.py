import logging
from typing import Optional
import asyncpg

logger = logging.getLogger(__name__)


class ChunkRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def bulk_insert(self, chunks: list[dict]) -> list[str]:
        """
        Bulk insert chunks using COPY for maximum throughput.

        Each chunk dict may contain:
            chunk_id (str), parent_document_id (str), chunk_index (int),
            chunk_text (str), char_start (int), char_end (int),
            page_number (int), source_type (str), token_count (int),
            language (str), chunk_metadata (dict), embedding_status (str)

        Returns list of inserted chunk_ids in insertion order.
        """
        if not chunks:
            return []

        import json

        records = [
            (
                c['chunk_id'],
                c['parent_document_id'],
                c['chunk_index'],
                c['chunk_text'],
                c.get('char_start'),
                c.get('char_end'),
                c.get('page_number'),
                c.get('source_type', 'text'),
                c.get('token_count'),
                c.get('language'),
                json.dumps(c.get('chunk_metadata', {})),
                c.get('embedding_status', 'pending'),
            )
            for c in chunks
        ]

        async with self.pool.acquire() as conn:
            await conn.copy_records_to_table(
                'chunks',
                records=records,
                columns=[
                    'chunk_id',
                    'parent_document_id',
                    'chunk_index',
                    'chunk_text',
                    'char_start',
                    'char_end',
                    'page_number',
                    'source_type',
                    'token_count',
                    'language',
                    'chunk_metadata',
                    'embedding_status',
                ],
            )

        logger.debug(f"Bulk inserted {len(chunks)} chunks")
        return [c['chunk_id'] for c in chunks]

    async def fetch_by_ids(self, chunk_ids: list[str]) -> list[dict]:
        """Fetch chunks by a list of chunk_ids. Order is not guaranteed."""
        if not chunk_ids:
            return []

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT chunk_id::text, parent_document_id::text, chunk_index,
                       chunk_text, char_start, char_end, page_number,
                       source_type, token_count, language, chunk_metadata,
                       embedding_status
                FROM chunks
                WHERE chunk_id = ANY($1::uuid[])
                """,
                chunk_ids,
            )
        return [dict(r) for r in rows]

    async def list_by_document(self, document_id: str) -> list[dict]:
        """Return all chunks for a document ordered by chunk_index."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT chunk_id::text, parent_document_id::text, chunk_index,
                       chunk_text, char_start, char_end, page_number,
                       source_type, token_count, language, chunk_metadata,
                       embedding_status
                FROM chunks
                WHERE parent_document_id=$1::uuid
                ORDER BY chunk_index ASC
                """,
                document_id,
            )
        return [dict(r) for r in rows]

    async def bulk_update_status(self, chunk_ids: list[str], status: str) -> None:
        """Update the embedding_status column for a batch of chunks."""
        if not chunk_ids:
            return

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE chunks
                SET embedding_status=$1
                WHERE chunk_id = ANY($2::uuid[])
                """,
                status,
                chunk_ids,
            )
        logger.debug(f"Updated {len(chunk_ids)} chunks to embedding_status='{status}'")

    async def fetch_all_for_bm25(self, limit: int = 5_000_000) -> list[dict]:
        """
        Fetch chunk_id, parent_document_id, and chunk_text for BM25 index construction.
        Only returns chunks with embedding_status='done' to ensure consistency with
        the vector index.  Streams results via server-side cursor to avoid loading
        all rows into memory at once.
        """
        results: list[dict] = []

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                async for row in conn.cursor(
                    """
                    SELECT chunk_id::text,
                           parent_document_id::text,
                           chunk_text
                    FROM chunks
                    WHERE embedding_status = 'done'
                    ORDER BY chunk_id
                    LIMIT $1
                    """,
                    limit,
                    prefetch=5000,
                ):
                    results.append(dict(row))

        logger.info(f"Fetched {len(results)} chunks for BM25 index")
        return results

    async def count_by_embedding_status(self, document_id: str) -> dict[str, int]:
        """Return chunk counts grouped by embedding_status for a document."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT embedding_status, COUNT(*) AS cnt
                FROM chunks
                WHERE parent_document_id = $1::uuid
                GROUP BY embedding_status
                """,
                document_id,
            )
            return {r['embedding_status']: r['cnt'] for r in rows}

    async def count_all_by_embedding_status(self) -> dict[str, int]:
        """Return global chunk counts grouped by embedding_status."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT embedding_status, COUNT(*) AS cnt FROM chunks GROUP BY embedding_status",
            )
            return {r['embedding_status']: r['cnt'] for r in rows}

    async def delete_by_document(self, document_id: str) -> int:
        """Hard-delete all chunks for a document.

        Note: chunk_embeddings rows are deleted automatically via ON DELETE CASCADE.
        via EmbeddingRepository.delete_by_document().
        Returns the number of chunk rows deleted.
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM chunks WHERE parent_document_id = $1::uuid",
                document_id,
            )
        deleted_count = int(result.split()[-1])
        logger.info(f"Hard-deleted {deleted_count} chunks for document {document_id}")
        return deleted_count
