#!/usr/bin/env python3
"""
db_ensure.py — Idempotent database bootstrap for the RAG backend.

Runs at container startup (before uvicorn) to guarantee that:
  1. Required PostgreSQL extensions exist  (uuid-ossp, vector).
  2. All schema objects exist              (tables, indexes, triggers, functions).

All DDL is guarded by IF NOT EXISTS / CREATE OR REPLACE, so this is
safe to call on every startup against both a fresh and an already-initialised
database — including an external managed database.

Environment variables
---------------------
DATABASE_URL        Required. Application connection string.
                    postgresql://user:pass@host:5432/dbname

DATABASE_ADMIN_URL  Optional. Superuser connection string used only for
                    CREATE EXTENSION.  Required on managed databases (AWS RDS,
                    Google Cloud SQL, Azure Database, Supabase, etc.) where the
                    application user does not have SUPERUSER.
                    If unset, extension creation is attempted via DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [db-ensure] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection strings
# ---------------------------------------------------------------------------

DATABASE_URL       = os.environ.get("DATABASE_URL", "")
DATABASE_ADMIN_URL = os.environ.get("DATABASE_ADMIN_URL", "")

# ---------------------------------------------------------------------------
# Extensions DDL  (run via admin / superuser connection)
# ---------------------------------------------------------------------------

_EXTENSIONS_DDL = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
"""

# ---------------------------------------------------------------------------
# Schema DDL  (idempotent; run via application user connection)
#
# Changes vs infra/postgres/01_init.sql:
#   • CREATE TABLE → CREATE TABLE IF NOT EXISTS
#   • CREATE INDEX / CREATE UNIQUE INDEX → IF NOT EXISTS
#   • CREATE TRIGGER → CREATE OR REPLACE TRIGGER  (requires PostgreSQL 14+)
# ---------------------------------------------------------------------------

_SCHEMA_DDL = """
-- ── Utility function ──────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- ── Table: parent_documents ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS parent_documents (
    parent_document_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename             TEXT NOT NULL,
    s3_bucket            TEXT NOT NULL,
    s3_key               TEXT NOT NULL,
    s3_uri               TEXT GENERATED ALWAYS AS ('s3://' || s3_bucket || '/' || s3_key) STORED,
    file_size_bytes      BIGINT,
    mime_type            TEXT DEFAULT 'application/pdf',
    page_count           INT,
    has_text             BOOLEAN DEFAULT FALSE,
    has_images           BOOLEAN DEFAULT FALSE,
    language_detected    TEXT,
    status               TEXT DEFAULT 'pending'
                         CHECK (status IN ('pending','ingesting','chunking','embedding','ready','failed','on_hold')),
    error_message        TEXT,
    retry_count          INT DEFAULT 0,
    source_metadata      JSONB DEFAULT '{}',
    sha256_hash          TEXT,
    created_at           TIMESTAMPTZ DEFAULT now(),
    updated_at           TIMESTAMPTZ DEFAULT now(),
    completed_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pd_status  ON parent_documents(status);
CREATE INDEX IF NOT EXISTS idx_pd_created ON parent_documents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pd_sha256  ON parent_documents(sha256_hash);

-- Unique constraint on content hash (skip deleted/null)
CREATE UNIQUE INDEX IF NOT EXISTS idx_pd_sha256_unique
    ON parent_documents(sha256_hash)
    WHERE error_message IS DISTINCT FROM 'deleted'
      AND sha256_hash IS NOT NULL;

CREATE OR REPLACE TRIGGER update_parent_documents_updated_at
    BEFORE UPDATE ON parent_documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── Table: chunks ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_document_id   UUID NOT NULL REFERENCES parent_documents(parent_document_id) ON DELETE CASCADE,
    chunk_index          INT NOT NULL,
    chunk_text           TEXT NOT NULL,
    char_start           INT,
    char_end             INT,
    page_number          INT,
    source_type          TEXT DEFAULT 'text' CHECK (source_type IN ('text','ocr','mixed')),
    token_count          INT,
    language             TEXT,
    chunk_metadata       JSONB DEFAULT '{}',
    embedding_status     TEXT DEFAULT 'pending'
                         CHECK (embedding_status IN ('pending','processing','done','failed')),
    created_at           TIMESTAMPTZ DEFAULT now(),
    updated_at           TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunks_parent ON chunks(parent_document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(embedding_status);
CREATE INDEX IF NOT EXISTS idx_chunks_fts    ON chunks USING gin(to_tsvector('simple', chunk_text));

CREATE OR REPLACE TRIGGER update_chunks_updated_at
    BEFORE UPDATE ON chunks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── Table: chunk_embeddings (pgvector) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id             UUID PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    parent_document_id   UUID NOT NULL,
    embedding            vector(768) NOT NULL,
    model_name           TEXT NOT NULL DEFAULT 'bert-base-uncased-int8',
    model_version        TEXT NOT NULL DEFAULT 'local',
    created_at           TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_embeddings_parent ON chunk_embeddings(parent_document_id);

-- HNSW index: CREATE INDEX IF NOT EXISTS is supported for HNSW in pgvector 0.5+
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw ON chunk_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── Table: retrieval_audit ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS retrieval_audit (
    audit_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_raw         TEXT NOT NULL,
    query_processed   TEXT,
    entities_detected JSONB DEFAULT '[]',
    query_embedding   TEXT,
    retrieval_mode    TEXT CHECK (retrieval_mode IN ('k_chunks','n_documents')),
    k_requested       INT,
    n_requested       INT,
    dense_candidates  JSONB DEFAULT '[]',
    sparse_candidates JSONB DEFAULT '[]',
    rrf_scores        JSONB DEFAULT '[]',
    mmr_selected      JSONB DEFAULT '[]',
    final_ranked      JSONB DEFAULT '[]',
    latency_ms        INT,
    client_ip         INET,
    api_key_hash      TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON retrieval_audit(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_api_key ON retrieval_audit(api_key_hash);
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _asyncpg_dsn(url: str) -> str:
    """Convert postgresql:// → postgresql:// (asyncpg accepts both forms)."""
    return url.replace("postgres://", "postgresql://", 1)


async def _ensure_extensions(dsn: str) -> None:
    """
    Create required PostgreSQL extensions.

    This step requires SUPERUSER or, on managed databases, the rds_superuser /
    cloudsqlsuperuser role.  Pass DATABASE_ADMIN_URL to use a privileged account
    only for this step.
    """
    conn = await asyncpg.connect(_asyncpg_dsn(dsn))
    try:
        for stmt in _EXTENSIONS_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(stmt)
        logger.info("Extensions verified: uuid-ossp, vector")
    except asyncpg.InsufficientPrivilegeError as exc:
        logger.error(
            "Insufficient privilege to CREATE EXTENSION. "
            "Set DATABASE_ADMIN_URL to a superuser/admin connection string, "
            "or create the extensions manually: "
            "  CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; "
            "  CREATE EXTENSION IF NOT EXISTS vector; "
            f"Detail: {exc}"
        )
        raise
    finally:
        await conn.close()


async def _ensure_schema(dsn: str) -> None:
    """Apply schema DDL idempotently via the application user."""
    conn = await asyncpg.connect(_asyncpg_dsn(dsn))
    try:
        await conn.execute(_SCHEMA_DDL)
        logger.info("Schema verified: parent_documents, chunks, chunk_embeddings, retrieval_audit")
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    if not DATABASE_URL:
        logger.error("DATABASE_URL is not set — cannot initialise the database.")
        sys.exit(1)

    ext_dsn = DATABASE_ADMIN_URL if DATABASE_ADMIN_URL else DATABASE_URL
    if DATABASE_ADMIN_URL:
        logger.info("Using DATABASE_ADMIN_URL for extension creation.")
    else:
        logger.info(
            "DATABASE_ADMIN_URL not set — attempting extension creation "
            "via DATABASE_URL (requires SUPERUSER or equivalent)."
        )

    logger.info("Running database bootstrap...")

    await _ensure_extensions(ext_dsn)
    await _ensure_schema(DATABASE_URL)

    logger.info("Database bootstrap complete.")


if __name__ == "__main__":
    asyncio.run(main())
