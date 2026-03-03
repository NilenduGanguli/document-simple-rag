# PostgreSQL Setup — Dev Environment

This document covers how the database is initialised in the bundled dev stack,
what roles and extensions are required, and how to swap in an external (managed)
PostgreSQL instance instead.

---

## 1. How the bundled postgres container works

### Image

`docker/postgres/Dockerfile` extends the official `pgvector/pgvector:pg16` image,
which ships PostgreSQL 16 with the `vector` extension pre-compiled.

```
FROM pgvector/pgvector:pg16
COPY infra/postgres/01_init.sql /docker-entrypoint-initdb.d/01_init.sql
```

The official postgres image runs every `*.sql` and `*.sh` file placed in
`/docker-entrypoint-initdb.d/` automatically on **first boot** — specifically
only when `PGDATA` is empty (a fresh volume). Subsequent restarts skip this
directory entirely.

### What happens on first boot

1. The postgres image entrypoint calls `initdb` and creates the cluster.
2. It creates the role and database declared in the environment variables:
   ```
   POSTGRES_USER     = raguser
   POSTGRES_PASSWORD = ragpassword123   (or $POSTGRES_PASSWORD)
   POSTGRES_DB       = ragdb
   ```
3. It executes `01_init.sql` as the `postgres` superuser against `ragdb`.

### 01_init.sql — what it creates

| Object | Details |
|---|---|
| **Extensions** | `uuid-ossp` (UUID generation), `vector` (pgvector 768-dim) |
| **Function** | `update_updated_at_column()` — trigger function that stamps `updated_at` |
| **`parent_documents`** | One row per uploaded file; tracks pipeline status, S3 location, SHA-256 |
| **`chunks`** | Text/OCR chunks derived from each document; FK → `parent_documents` (CASCADE) |
| **`chunk_embeddings`** | `vector(768)` column; one row per chunk; FK → `chunks` (CASCADE) |
| **`retrieval_audit`** | Full log of every retrieval request with scores and latency |
| **Indexes** | B-tree on status/created_at/sha256; GIN for full-text search; **HNSW** on `chunk_embeddings.embedding` (`m=16, ef_construction=64`) |
| **Triggers** | `updated_at` auto-stamp on `parent_documents` and `chunks` |

The `sha256_hash` unique index prevents duplicate document ingestion for
non-deleted documents.

### Role and privileges

The `POSTGRES_USER` (`raguser`) is the database owner and has full access
to all objects in the `public` schema.  In the bundled container this user
is effectively the superuser of `ragdb`.

---

## 2. Backend startup bootstrap (`db_ensure.py`)

Every time the backend container starts, `start.sh` runs
`python /app/db_ensure.py` before launching uvicorn.

**What it does:**

1. Connects to the *extension DSN* (`DATABASE_ADMIN_URL` if set, otherwise
   `DATABASE_URL`) and runs:
   ```sql
   CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
2. Connects to `DATABASE_URL` and applies a fully idempotent version of the
   schema DDL (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`,
   `CREATE OR REPLACE TRIGGER`, etc.).

The script is a no-op when the schema already exists — safe on every restart.
It exits with a non-zero code if `DATABASE_URL` is unset or if extension
creation fails (which stops the container, surfacing the problem immediately).

---

## 3. Using an external PostgreSQL in dev

### Environment variables to change

Remove (or stop) the `postgres` and `minio-init` services and point the
backend at your external database by setting these variables when starting the
stack.

| Variable | Bundled default | External value |
|---|---|---|
| `DATABASE_URL` | `postgresql://raguser:ragpassword123@postgres:5432/ragdb` | Your external connection string |
| `DATABASE_ADMIN_URL` | *(unset — same user has superuser rights)* | Superuser DSN (see below) |

**Remove the postgres dependency from docker-compose** by temporarily
overriding `depends_on` or using an override file, **or** simply bring up only
the non-postgres services:

```bash
OPENAI_API_KEY=... \
DATABASE_URL=postgresql://raguser:secret@my-db.example.com:5432/ragdb \
DATABASE_ADMIN_URL=postgresql://postgres:adminpass@my-db.example.com:5432/ragdb \
docker compose -f docker-compose.4container.yml \
  up minio minio-init ocr-api backend frontend
```

The backend depends on `postgres: condition: service_healthy` in the compose
file.  When skipping the bundled container, either:
- Remove the `ocr-api` dependency line for the external run, or
- Use a compose override to drop the `depends_on.postgres` condition.

### Required setup on the external database

Before the backend can connect the following must be true:

#### 1. Database and role exist

```sql
-- Run as your admin user
CREATE ROLE raguser WITH LOGIN PASSWORD 'your-password';
CREATE DATABASE ragdb OWNER raguser;
GRANT ALL PRIVILEGES ON DATABASE ragdb TO raguser;
```

#### 2. Extensions enabled

The `vector` extension requires the pgvector binary to be present on the
server — it cannot be installed from client side alone.

| Platform | How to enable pgvector |
|---|---|
| **AWS RDS PostgreSQL 15+** | Set parameter group `rds.extensions = vector` (or use `CREATE EXTENSION` — it's allowed for `rds_superuser`) |
| **AWS Aurora PostgreSQL** | Same as RDS; pgvector bundled since PostgreSQL 14.4 |
| **Google Cloud SQL** | pgvector available since PostgreSQL 15; enable via `CREATE EXTENSION vector` as `cloudsqlsuperuser` |
| **Azure Database for PostgreSQL** | Flexible Server — pgvector bundled; enable via `CREATE EXTENSION vector` as the admin user |
| **Supabase** | pgvector enabled by default in all projects |
| **Self-hosted** | Install the `pgvector` OS package matching your PG version, then `CREATE EXTENSION vector` |

The `db_ensure.py` script attempts extension creation via `DATABASE_ADMIN_URL`.
Set it to a connection string for an account with `SUPERUSER` or the
platform-specific admin role (e.g. `rds_superuser`).

If you prefer doing this step manually (once), run:

```sql
-- Connect as superuser / rds_superuser / cloudsqlsuperuser
\c ragdb
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
```

Then unset `DATABASE_ADMIN_URL` — subsequent `db_ensure.py` runs will see the
extensions already exist and skip creation.

#### 3. Grants after schema bootstrap

`db_ensure.py` creates all objects **as** `raguser`, so no additional `GRANT`
statements are needed.  If the extensions were created by a different superuser,
run once:

```sql
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO raguser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO raguser;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO raguser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES    TO raguser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO raguser;
```

---

## 4. Connection string format

```
postgresql://<user>:<password>@<host>:<port>/<database>
```

| Component | Bundled dev | Example external |
|---|---|---|
| user | `raguser` | `raguser` |
| password | `ragpassword123` | your password |
| host | `postgres` (Docker service name) | `my-db.example.com` or `127.0.0.1` |
| port | `5432` | `5432` (RDS default) |
| database | `ragdb` | `ragdb` |

For SSL (required on managed databases):

```
postgresql://raguser:pass@host:5432/ragdb?sslmode=require
```

---

## 5. Schema diagram (abbreviated)

```
parent_documents
├── parent_document_id  UUID PK
├── filename, s3_bucket, s3_key, s3_uri
├── sha256_hash         UNIQUE (non-deleted)
├── status              pending | ingesting | chunking | embedding | ready | failed | on_hold
└── page_count, has_text, has_images, ...

chunks  (FK → parent_documents ON DELETE CASCADE)
├── chunk_id            UUID PK
├── parent_document_id  UUID FK
├── chunk_text          TEXT   ← GIN full-text index (simple)
├── source_type         text | ocr | mixed
└── embedding_status    pending | processing | done | failed

chunk_embeddings  (FK → chunks ON DELETE CASCADE)
├── chunk_id            UUID PK FK
├── embedding           vector(768)   ← HNSW index (cosine, m=16)
├── model_name, model_version
└── parent_document_id  UUID

retrieval_audit
├── audit_id            UUID PK
├── query_raw, query_processed, entities_detected
├── dense_candidates, sparse_candidates, rrf_scores, final_ranked  JSONB
└── latency_ms, client_ip, api_key_hash
```

---

## 6. Verifying the setup

From any host with `psql` access:

```bash
# Check extensions
psql "$DATABASE_URL" -c "\dx"

# Check tables
psql "$DATABASE_URL" -c "\dt"

# Check HNSW index
psql "$DATABASE_URL" -c "\di idx_embeddings_hnsw"

# Quick row counts
psql "$DATABASE_URL" -c "
  SELECT 'parent_documents' AS t, COUNT(*) FROM parent_documents
  UNION ALL SELECT 'chunks',           COUNT(*) FROM chunks
  UNION ALL SELECT 'chunk_embeddings', COUNT(*) FROM chunk_embeddings;"
```

Or from inside the running backend container:

```bash
docker exec document-simple-rag-backend-1 \
  python -c "
import asyncio, asyncpg, os
async def check():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    rows = await conn.fetch('SELECT extname FROM pg_extension ORDER BY extname')
    print('Extensions:', [r[0] for r in rows])
    tables = await conn.fetch(\"SELECT tablename FROM pg_tables WHERE schemaname='public'\")
    print('Tables:', [r[0] for r in tables])
    await conn.close()
asyncio.run(check())
"
```
