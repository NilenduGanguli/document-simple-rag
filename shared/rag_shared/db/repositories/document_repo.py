from typing import Optional
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
                source_metadata or {},
            )
            return row['parent_document_id']

    async def update_status(
        self,
        document_id: str,
        status: str,
        error_message: str = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            if status == 'ready':
                await conn.execute(
                    """
                    UPDATE parent_documents
                    SET status=$1, error_message=$2, completed_at=now()
                    WHERE parent_document_id=$3::uuid
                    """,
                    status,
                    error_message,
                    document_id,
                )
            else:
                await conn.execute(
                    """
                    UPDATE parent_documents
                    SET status=$1, error_message=$2
                    WHERE parent_document_id=$3::uuid
                    """,
                    status,
                    error_message,
                    document_id,
                )

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
