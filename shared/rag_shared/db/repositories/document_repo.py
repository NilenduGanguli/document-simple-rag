from typing import Optional
import json
import asyncpg


class DocumentRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def create(
        self,
        filename: str,
        s3_bucket: str,
        s3_key: str,
        file_size_bytes: int,
        mime_type: str = 'application/pdf',
        sha256_hash: str = None,
        source_metadata: dict = None,
    ) -> str:
        """Insert new document record. Returns parent_document_id."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO parent_documents
                    (filename, s3_bucket, s3_key, file_size_bytes, mime_type,
                     sha256_hash, source_metadata, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, 'pending')
                RETURNING parent_document_id::text
                """,
                filename,
                s3_bucket,
                s3_key,
                file_size_bytes,
                mime_type,
                sha256_hash,
                json.dumps(source_metadata or {}),
            )
            return row['parent_document_id']

    async def update_status(
        self,
        document_id: str,
        status: str,
        error_message: str = None,
    ) -> bool:
        """Update document status. Returns False if the document is on_hold
        and a non-hold status was requested (prevents the worker from
        overwriting a hold placed by the admin).
        """
        async with self.pool.acquire() as conn:
            # When setting on_hold, always allow (admin action)
            if status == 'on_hold':
                result = await conn.execute(
                    """
                    UPDATE parent_documents
                    SET status=$1, error_message=$2
                    WHERE parent_document_id=$3::uuid
                    """,
                    status,
                    error_message,
                    document_id,
                )
            elif status == 'ready':
                result = await conn.execute(
                    """
                    UPDATE parent_documents
                    SET status=$1, error_message=$2, completed_at=now()
                    WHERE parent_document_id=$3::uuid
                      AND status != 'on_hold'
                    """,
                    status,
                    error_message,
                    document_id,
                )
            else:
                result = await conn.execute(
                    """
                    UPDATE parent_documents
                    SET status=$1, error_message=$2
                    WHERE parent_document_id=$3::uuid
                      AND status != 'on_hold'
                    """,
                    status,
                    error_message,
                    document_id,
                )
            return result == "UPDATE 1"

    async def mark_ready_if_complete(self, document_id: str) -> bool:
        """Atomically check if all chunks are embedded and mark the document
        as 'ready' if so.

        Uses ``SELECT … FOR UPDATE`` to prevent concurrent pods from both
        marking the same document ready.  Returns ``True`` only when THIS
        call performed the transition to 'ready'.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Lock the document row to serialise concurrent readiness checks
                doc = await conn.fetchrow(
                    """
                    SELECT status
                    FROM parent_documents
                    WHERE parent_document_id = $1::uuid
                    FOR UPDATE
                    """,
                    document_id,
                )
                if doc is None:
                    return False

                # Already terminal — nothing to do
                if doc['status'] in ('ready', 'on_hold', 'failed'):
                    return False

                # Count chunk embedding statuses within the same transaction
                counts = await conn.fetch(
                    """
                    SELECT embedding_status, COUNT(*) AS cnt
                    FROM chunks
                    WHERE parent_document_id = $1::uuid
                    GROUP BY embedding_status
                    """,
                    document_id,
                )
                status_map = {r['embedding_status']: r['cnt'] for r in counts}
                total = sum(status_map.values())
                done = status_map.get('done', 0)

                if total == 0 or done < total:
                    return False

                # All chunks are done — mark ready
                result = await conn.execute(
                    """
                    UPDATE parent_documents
                    SET status = 'ready', completed_at = now()
                    WHERE parent_document_id = $1::uuid
                      AND status != 'on_hold'
                      AND status != 'ready'
                    """,
                    document_id,
                )
                return result == "UPDATE 1"

    async def increment_retry(self, document_id: str) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE parent_documents
                SET retry_count=retry_count+1
                WHERE parent_document_id=$1::uuid
                RETURNING retry_count
                """,
                document_id,
            )
            return row['retry_count']

    async def get_by_id(self, document_id: str) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM parent_documents WHERE parent_document_id=$1::uuid",
                document_id,
            )
            return dict(row) if row else None

    async def get_by_sha256(self, sha256_hash: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT parent_document_id::text
                FROM parent_documents
                WHERE sha256_hash=$1
                  AND error_message IS DISTINCT FROM 'deleted'
                LIMIT 1
                """,
                sha256_hash,
            )
            return row['parent_document_id'] if row else None

    async def update_metadata(
        self,
        document_id: str,
        page_count: int = None,
        has_text: bool = None,
        has_images: bool = None,
        language_detected: str = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE parent_documents
                SET page_count=COALESCE($1, page_count),
                    has_text=COALESCE($2, has_text),
                    has_images=COALESCE($3, has_images),
                    language_detected=COALESCE($4, language_detected)
                WHERE parent_document_id=$5::uuid
                """,
                page_count,
                has_text,
                has_images,
                language_detected,
                document_id,
            )

    async def soft_delete(self, document_id: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE parent_documents
                SET status='failed', error_message='deleted'
                WHERE parent_document_id=$1::uuid
                """,
                document_id,
            )
            return result == "UPDATE 1"

    async def list_all(
        self,
        limit: int = 50,
        offset: int = 0,
        status_filter: str = None,
    ) -> tuple[list[dict], int]:
        """List documents with pagination and optional status filter.
        Excludes soft-deleted documents. Returns (rows, total_count)."""
        async with self.pool.acquire() as conn:
            count_row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt FROM parent_documents
                WHERE ($1::text IS NULL OR status = $1)
                  AND error_message IS DISTINCT FROM 'deleted'
                """,
                status_filter,
            )
            total = count_row['cnt']

            rows = await conn.fetch(
                """
                SELECT pd.parent_document_id::text, pd.filename, pd.status,
                       pd.page_count, pd.file_size_bytes, pd.created_at,
                       pd.updated_at, pd.completed_at, pd.error_message,
                       COUNT(c.chunk_id) AS chunk_count
                FROM parent_documents pd
                LEFT JOIN chunks c ON c.parent_document_id = pd.parent_document_id
                WHERE ($1::text IS NULL OR pd.status = $1)
                  AND pd.error_message IS DISTINCT FROM 'deleted'
                GROUP BY pd.parent_document_id
                ORDER BY pd.created_at DESC
                LIMIT $2 OFFSET $3
                """,
                status_filter,
                limit,
                offset,
            )
            return [dict(r) for r in rows], total

    async def count_by_status(self) -> dict[str, int]:
        """Return document counts grouped by status."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT status, COUNT(*) AS cnt
                FROM parent_documents
                WHERE error_message IS DISTINCT FROM 'deleted'
                GROUP BY status
                """,
            )
            return {r['status']: r['cnt'] for r in rows}

    async def get_pipeline_details(self, document_id: str) -> Optional[dict]:
        """Get document with chunk/embedding breakdowns in a single query."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT pd.*,
                       COUNT(c.chunk_id) AS total_chunks,
                       COUNT(c.chunk_id) FILTER (WHERE c.embedding_status = 'pending') AS chunks_pending,
                       COUNT(c.chunk_id) FILTER (WHERE c.embedding_status = 'processing') AS chunks_processing,
                       COUNT(c.chunk_id) FILTER (WHERE c.embedding_status = 'done') AS chunks_done,
                       COUNT(c.chunk_id) FILTER (WHERE c.embedding_status = 'failed') AS chunks_failed,
                       COUNT(c.chunk_id) FILTER (WHERE c.embedding_status = 'done') AS total_embeddings
                FROM parent_documents pd
                LEFT JOIN chunks c ON c.parent_document_id = pd.parent_document_id
                WHERE pd.parent_document_id = $1::uuid
                GROUP BY pd.parent_document_id
                """,
                document_id,
            )
            return dict(row) if row else None

    async def reset_for_reprocess(self, document_id: str) -> bool:
        """Reset document state for reprocessing.

        Clears status back to 'pending', removes error_message, resets
        retry_count and completed_at so the document flows through the
        ingestion pipeline from scratch.  Returns True if a row was updated.
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE parent_documents
                SET status='pending',
                    error_message=NULL,
                    retry_count=0,
                    completed_at=NULL
                WHERE parent_document_id=$1::uuid
                """,
                document_id,
            )
            return result == "UPDATE 1"

    async def hold_document(self, document_id: str) -> bool:
        """Place a document on hold. Returns True if a row was updated."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE parent_documents
                SET status='on_hold',
                    error_message='Placed on hold by admin'
                WHERE parent_document_id=$1::uuid
                """,
                document_id,
            )
            return result == "UPDATE 1"
