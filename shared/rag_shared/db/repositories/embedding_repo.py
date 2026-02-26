from typing import Optional
import asyncpg
import logging

logger = logging.getLogger(__name__)


class EmbeddingRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def bulk_upsert(
        self,
        chunk_ids: list[str],
        parent_doc_ids: list[str],
        embeddings: list[list[float]],
        model_name: str,
        model_version: str,
    ) -> None:
        """
        Upsert embeddings into chunk_embeddings table.

        Embeddings are formatted as pgvector literal strings ('[1.0,2.0,...,768.0]')
        because asyncpg does not natively support the vector type.  The CAST is
        performed inside the SQL expression so asyncpg sends text and Postgres
        converts it.

        Uses an explicit temporary table + INSERT … ON CONFLICT to keep a single
        round-trip for the entire batch.
        """
        if not chunk_ids:
            return

        if len(chunk_ids) != len(parent_doc_ids) or len(chunk_ids) != len(embeddings):
            raise ValueError(
                "chunk_ids, parent_doc_ids and embeddings must have the same length"
            )

        # Format each embedding as pgvector text literal
        vector_strings: list[str] = [
            '[' + ','.join(str(v) for v in emb) + ']'
            for emb in embeddings
        ]

        records = list(
            zip(chunk_ids, parent_doc_ids, vector_strings)
        )

        async with self.pool.acquire() as conn:
            # Use executemany with a parameterised upsert.  The vector column
            # is cast from text inside the VALUES expression.
            await conn.executemany(
                """
                INSERT INTO chunk_embeddings
                    (chunk_id, parent_document_id, embedding, model_name, model_version)
                VALUES ($1::uuid, $2::uuid, $3::vector, $4, $5)
                ON CONFLICT (chunk_id) DO UPDATE
                    SET embedding     = EXCLUDED.embedding,
                        model_name    = EXCLUDED.model_name,
                        model_version = EXCLUDED.model_version,
                        created_at    = now()
                """,
                [
                    (chunk_id, parent_doc_id, vector_str, model_name, model_version)
                    for chunk_id, parent_doc_id, vector_str in records
                ],
            )

        logger.debug(
            f"Upserted {len(chunk_ids)} embeddings "
            f"(model={model_name}, version={model_version})"
        )

    async def get_for_document(self, document_id: str) -> list[dict]:
        """
        Return all embeddings for a given parent document.

        The embedding value is returned as a string in pgvector bracket notation
        ('[0.1,0.2,...]').  Callers that need a numpy array should parse it
        themselves.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT ce.chunk_id::text,
                       ce.parent_document_id::text,
                       ce.embedding::text,
                       ce.model_name,
                       ce.model_version,
                       ce.created_at
                FROM chunk_embeddings ce
                WHERE ce.parent_document_id = $1::uuid
                ORDER BY ce.chunk_id
                """,
                document_id,
            )
        return [dict(r) for r in rows]
