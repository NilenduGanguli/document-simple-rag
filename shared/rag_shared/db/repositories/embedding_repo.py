"""Embedding repository backed by PostgreSQL pgvector."""
import logging

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingRepository:
    """Store and query vector embeddings in the chunk_embeddings table (pgvector)."""

    def __init__(self, db_pool) -> None:
        self.db_pool = db_pool

    async def bulk_upsert(
        self,
        chunk_ids: list[str],
        parent_doc_ids: list[str],
        embeddings: list[list[float]],
        model_name: str,
        model_version: str,
    ) -> None:
        """Upsert embeddings into the chunk_embeddings table."""
        if not chunk_ids:
            return

        if len(chunk_ids) != len(parent_doc_ids) or len(chunk_ids) != len(embeddings):
            raise ValueError(
                "chunk_ids, parent_doc_ids and embeddings must have the same length"
            )

        rows = [
            (cid, pid, np.array(emb, dtype=np.float32), model_name, model_version)
            for cid, pid, emb in zip(chunk_ids, parent_doc_ids, embeddings)
        ]

        async with self.db_pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO chunk_embeddings
                    (chunk_id, parent_document_id, embedding, model_name, model_version)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5)
                ON CONFLICT (chunk_id) DO UPDATE
                    SET embedding     = EXCLUDED.embedding,
                        model_name    = EXCLUDED.model_name,
                        model_version = EXCLUDED.model_version
                """,
                rows,
            )

        logger.debug(
            f"Upserted {len(chunk_ids)} embeddings to pgvector "
            f"(model={model_name}, version={model_version})"
        )

    async def delete_by_document(self, document_id: str) -> None:
        """Delete all embeddings for a document from chunk_embeddings."""
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM chunk_embeddings WHERE parent_document_id = $1::uuid",
                    document_id,
                )
            logger.debug(f"Deleted embeddings for document {document_id} from pgvector")
        except Exception as exc:
            logger.warning(f"pgvector delete for document {document_id} failed: {exc}")

    async def count(self) -> int:
        """Return total number of embeddings in the table."""
        async with self.db_pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM chunk_embeddings")
