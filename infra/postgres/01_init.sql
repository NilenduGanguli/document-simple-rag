-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Auto-update updated_at function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Table 1: parent_documents
CREATE TABLE parent_documents (
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
                         CHECK (status IN ('pending','ingesting','chunking','embedding','ready','failed')),
    error_message        TEXT,
    retry_count          INT DEFAULT 0,
    source_metadata      JSONB DEFAULT '{}',
    sha256_hash          TEXT,
    created_at           TIMESTAMPTZ DEFAULT now(),
    updated_at           TIMESTAMPTZ DEFAULT now(),
    completed_at         TIMESTAMPTZ
);
CREATE INDEX idx_pd_status   ON parent_documents(status);
CREATE INDEX idx_pd_created  ON parent_documents(created_at DESC);
CREATE INDEX idx_pd_sha256   ON parent_documents(sha256_hash);

CREATE TRIGGER update_parent_documents_updated_at
    BEFORE UPDATE ON parent_documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Table 2: chunks
CREATE TABLE chunks (
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
CREATE INDEX idx_chunks_parent ON chunks(parent_document_id);
CREATE INDEX idx_chunks_status ON chunks(embedding_status);
CREATE INDEX idx_chunks_fts    ON chunks USING gin(to_tsvector('simple', chunk_text));

CREATE TRIGGER update_chunks_updated_at
    BEFORE UPDATE ON chunks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Table 3: chunk_embeddings (PGVector)
CREATE TABLE chunk_embeddings (
    embedding_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id           UUID NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    parent_document_id UUID NOT NULL,
    embedding          vector(768),
    model_name         TEXT NOT NULL,
    model_version      TEXT,
    created_at         TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_emb_chunk ON chunk_embeddings(chunk_id);
CREATE UNIQUE INDEX uq_emb_chunk_id ON chunk_embeddings(chunk_id);
CREATE INDEX idx_emb_hnsw ON chunk_embeddings
    USING hnsw (embedding vector_cosine_ops) WITH (m=32, ef_construction=200);
-- Note: IVFFlat index requires data to be loaded first; create after initial data load.
-- CREATE INDEX idx_emb_ivfflat ON chunk_embeddings
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists=500);

-- Table 4: retrieval_audit
CREATE TABLE retrieval_audit (
    audit_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_raw         TEXT NOT NULL,
    query_processed   TEXT,
    entities_detected JSONB DEFAULT '[]',
    query_embedding   vector(768),
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
CREATE INDEX idx_audit_created ON retrieval_audit(created_at DESC);
CREATE INDEX idx_audit_api_key ON retrieval_audit(api_key_hash);
