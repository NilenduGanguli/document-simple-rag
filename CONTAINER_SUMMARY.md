# Container Summary — Document Simple RAG

## Infrastructure Containers (pre-built images, no build step)

### 1. `postgres`
- **Image**: `postgres:16`
- **Purpose**: Primary data store. Holds `parent_documents`, `chunks`, and `retrieval_audit` tables.
- **Port**: `15432 → 5432`
- **Volume**: `postgres_data:/var/lib/postgresql/data`
- **Bind mount**: `infra/postgres/01_init.sql` — runs on first start to create the schema (tables, indexes, triggers, the `uuid-ossp` extension, and the new partial unique index on `sha256_hash`).
- **Healthcheck**: `pg_isready -U raguser -d ragdb`

### 2. `postgres-exporter`
- **Image**: `prometheuscommunity/postgres-exporter:v0.15.0`
- **Purpose**: Exposes PostgreSQL metrics on `:9187/metrics` for Prometheus scraping.
- **Depends on**: `postgres` (healthy)

### 3. `redis`
- **Image**: `redis:7.2-alpine`
- **Purpose**: Multi-purpose cache shared across services via isolated DB numbers:
  - DB 0 — ingest-api + ingestion-worker (dedup keys, hold flags)
  - DB 1 — embedding-service (embedding cache)
  - DB 2 — retrieval-api (query caching, BM25 pub/sub)
- **Port**: `16379 → 6379`
- **Volume**: `redis_data:/data`
- **Bind mount**: `infra/redis/redis.conf` — sets `maxmemory 2gb`, `allkeys-lru` eviction, `appendonly yes`, and periodic RDB snapshots.
- **Healthcheck**: `redis-cli ping`

### 4. `rabbitmq`
- **Image**: `rabbitmq:4.1-management-alpine`
- **Purpose**: Message broker connecting the ingestion pipeline. Provides 3 exchanges and 7 queues:
  - `rag.ingestion` (direct) → `ingestion_queue` + `ingestion_dlq`
  - `rag.ocr` (direct) → `ocr_queue` + `ocr_dlq`
  - `rag.embedding` (direct) → `embedding_queue` + `embedding_dlq`
  - `rag.priority` (headers) → `ingestion_priority_queue`
- **Ports**: `5672` (AMQP), `15672` (management UI)
- **Volume**: `rabbitmq_data:/var/lib/rabbitmq`
- **Bind mounts**: `infra/rabbitmq/rabbitmq.conf` + `infra/rabbitmq/definitions.json` (pre-declares users, vhosts, exchanges, queues, bindings on startup).
- **Healthcheck**: `rabbitmq-diagnostics ping` (60s start period)

### 5. `minio`
- **Image**: `minio/minio:latest`
- **Purpose**: S3-compatible object storage for uploaded PDF documents.
- **Ports**: `19000 → 9000` (API), `19001 → 9001` (console)
- **Volume**: `minio_data:/data`
- **Healthcheck**: `curl -f http://localhost:9000/minio/health/live`

### 6. `minio-init`
- **Image**: `minio/mc:latest`
- **Purpose**: One-shot init container. Waits for MinIO to be healthy, then creates two buckets: `rag-documents` and `rag-models`.
- **Bind mount**: `infra/minio/init.sh`
- **Depends on**: `minio` (healthy)
- **Restart**: `no`

### 7. `chromadb`
- **Image**: `chromadb/chroma:latest`
- **Purpose**: Vector database storing chunk embeddings (768-dim BERT vectors). Used for dense retrieval (cosine similarity search).
- **Port**: `18200 → 8000`
- **Volume**: `chromadb_data:/chroma/chroma`
- **Env**: `IS_PERSISTENT=TRUE`, `ANONYMIZED_TELEMETRY=FALSE`
- **Healthcheck**: TCP check on port 8000

### 8. `jaeger`
- **Image**: `jaegertracing/all-in-one:1.57`
- **Purpose**: Distributed tracing. All application services send OTLP traces here. Visualizes end-to-end request flow across ingestion, embedding, and retrieval.
- **Ports**: `16686` (UI), `4317` (OTLP gRPC collector)

### 9. `prometheus`
- **Image**: `prom/prometheus:v2.52.0`
- **Purpose**: Metrics aggregation. Scrapes 9 targets every 15s: ingest-api, embedding-service, retrieval-api, ingestion-worker, ocr-service, ocr-api, rabbitmq, postgres-exporter, jaeger.
- **Port**: `9090`
- **Volume**: `prometheus_data:/prometheus`
- **Bind mount**: `infra/prometheus/prometheus.yml`

### 10. `grafana`
- **Image**: `grafana/grafana:10.4.0`
- **Purpose**: Monitoring dashboards. Pre-provisioned with a Prometheus datasource and a `rag_pipeline.json` dashboard.
- **Port**: `3000`
- **Volume**: `grafana_data:/var/lib/grafana`
- **Bind mounts**: `infra/grafana/provisioning/` (datasource + dashboard provisioning), `infra/grafana/dashboards/` (JSON dashboard definitions)
- **Depends on**: `prometheus`

---

## Application Containers (custom-built images)

### 11. `model-init`
- **Base image**: `python:3.12-slim`
- **Purpose**: One-shot init container. Downloads a BERT model from HuggingFace (default `google-bert/bert-base-uncased`), exports it to ONNX FP32, then quantizes to INT8. Produces 3 copies (embedding, crossencoder, NER) with tokenizer files into the shared `models_volume`. Writes a `.ready` sentinel file — subsequent starts skip re-initialization.
- **Build context**: `.` (project root)
- **Dockerfile**: `services/model-init/Dockerfile`
- **Code copied**:
  - `shared/` → `/shared/` (installed as pip package `rag-shared`)
  - `services/model-init/requirements.txt` → pip install (`transformers`, `optimum`, `onnxruntime`, `torch`, `sentencepiece`, `numpy`)
  - `services/model-init/model_init.py` → `/app/model_init.py`
  - `services/model-init/set-env.sh` → `/app/set-env.sh`
- **Volume**: `models_volume:/models` (read-write)
- **Restart**: `no`
- **CMD**: `. /app/set-env.sh && exec python model_init.py`

### 12. `ingest-api`
- **Base image**: `python:3.12-slim`
- **Purpose**: FastAPI HTTP service (port 8000). Accepts PDF uploads via `POST /api/v1/documents/ingest`. Validates files (magic bytes, size), computes SHA-256, deduplicates via Redis → Postgres fallback, streams to MinIO, inserts a `parent_documents` row, publishes an `IngestionTask` to RabbitMQ. Also provides GET/DELETE/reprocess/hold/resume endpoints for document management.
- **Build context**: `.` (project root)
- **Dockerfile**: `services/ingest-api/Dockerfile`
- **Code copied**:
  - `shared/` → installed as `rag-shared` package
  - `services/ingest-api/requirements.txt` → pip install (`fastapi`, `uvicorn`, `aio-pika`, `aioboto3`, `msgpack`, `chromadb`)
  - `services/ingest-api/app/` → `/app/app/` (contains `main.py`, `schemas.py`, `routers/`, `middleware/`)
  - `services/ingest-api/set-env.sh`
- **Port**: `18000 → 8000`
- **Depends on**: postgres (healthy), redis (healthy), rabbitmq (healthy), minio-init (completed)
- **Healthcheck**: `curl -f http://localhost:8000/api/v1/health`
- **CMD**: `. /app/set-env.sh && exec uvicorn app.main:app --host 0.0.0.0 --port 8000`

### 13. `ingestion-worker`
- **Base image**: `python:3.12-slim`
- **Purpose**: Async worker consuming `ingestion_queue` from RabbitMQ. Downloads PDF from MinIO, extracts text via PyMuPDF, optionally sends pages for OCR, applies recursive text chunking (configurable token size/overlap), inserts chunks into Postgres, and publishes `EmbeddingTask` messages for each chunk batch. Runs as a long-lived consumer with configurable concurrency.
- **Build context**: `.` (project root)
- **Dockerfile**: `services/ingestion-worker/Dockerfile`
- **System deps**: `gcc`, `libmupdf-dev`
- **Code copied**:
  - `shared/` → installed as `rag-shared`
  - `services/ingestion-worker/requirements.txt` → pip install (`PyMuPDF`, `transformers`, `tokenizers`, `sentencepiece`, `aiohttp`)
  - `services/ingestion-worker/app/` → `/app/app/` (contains `main.py`, `worker.py`, `router.py`, `preprocessor.py`, `chunking/`)
  - `services/ingestion-worker/set-env.sh`
- **Volume**: `models_volume:/models:ro` (reads tokenizer for token counting)
- **Depends on**: postgres, redis, rabbitmq (healthy), minio-init (completed), model-init (completed)
- **CMD**: `. /app/set-env.sh && exec python -m app.main`

### 14. `ocr-service`
- **Base image**: `python:3.12-slim`
- **Purpose**: Consumes `ocr_queue` from RabbitMQ. Runs Tesseract OCR on document page images. Supports multiple languages (eng, fra, deu, spa, chi-sim). Can optionally delegate to the external `ocr-api` (OpenAI Vision) when `USE_OCR_API=true`.
- **Build context**: `.` (project root)
- **Dockerfile**: `services/ocr-service/Dockerfile`
- **System deps**: `tesseract-ocr` + 5 language packs, `libgl1`, `libglib2.0-0`
- **Code copied**:
  - `shared/` → installed as `rag-shared`
  - `services/ocr-service/requirements.txt` → pip install (`pytesseract`, `Pillow`, `httpx`)
  - `services/ocr-service/app/` → `/app/app/` (contains `main.py`, `processor.py`)
  - `services/ocr-service/set-env.sh`
- **Depends on**: redis (healthy), rabbitmq (healthy)
- **CMD**: `. /app/set-env.sh && exec python -m app.main`

### 15. `ocr-api`
- **Base image**: `python:3.11-slim` (note: 3.11 not 3.12)
- **Purpose**: Standalone FastAPI OCR API (port 8002) that uses OpenAI Vision to extract text from document images. Acts as an alternative backend to Tesseract when `ocr-service` has `USE_OCR_API=true`. Also usable independently.
- **Build context**: `./ocr-api` (its own directory, not project root)
- **Dockerfile**: `ocr-api/Dockerfile`
- **Code copied** (via `COPY . .`):
  - `ocr-api/main.py` — full FastAPI application
  - `ocr-api/requirements.txt` → pip install (`fastapi`, `uvicorn`, `openai`, `Pillow`, `requests`, `PyMuPDF`, `prometheus_client`, all OpenTelemetry packages)
  - `ocr-api/set-env.sh` — used as `ENTRYPOINT` (sources `.env` if present, then runs `exec "$@"`)
  - `ocr-api/.env` — contains `OPENAI_API_KEY`
- **Port**: `8002`
- **Healthcheck**: `curl -f http://localhost:8002/health`
- **CMD**: `uvicorn main:app --host 0.0.0.0 --port 8002`

### 16. `embedding-service`
- **Base image**: `python:3.12-slim`
- **Purpose**: Consumes `embedding_queue` from RabbitMQ. Two-stage pipeline:
  1. **Prefetch loop**: Reads chunk IDs from messages, checks Redis embedding cache, fetches uncached chunk texts from Postgres.
  2. **Embed-and-store loop**: Tokenizes text with BERT tokenizer, runs INT8 ONNX inference (in threadpool), mean-pools + L2-normalizes output, bulk-upserts to ChromaDB, caches in Redis, marks chunks as `done`, atomically marks parent documents `ready` when all chunks complete, and publishes BM25 refresh notification via Redis pub/sub.
- **Build context**: `.` (project root)
- **Dockerfile**: `services/embedding-service/Dockerfile`
- **System deps**: `gcc`
- **Code copied**:
  - `shared/` → installed as `rag-shared`
  - `services/embedding-service/requirements.txt` → pip install (`fastapi`, `uvicorn`, `onnxruntime`, `transformers`, `tokenizers`, `sentencepiece`, `numpy`, `chromadb`)
  - `services/embedding-service/app/` → `/app/app/` (contains `main.py`, `startup.py`, `worker.py`)
  - `services/embedding-service/set-env.sh`
- **Volume**: `models_volume:/models:ro` (reads INT8 ONNX model + tokenizer)
- **Depends on**: postgres, redis, rabbitmq, chromadb (healthy), model-init (completed)
- **CMD**: `. /app/set-env.sh && exec python -m app.main`

### 17. `retrieval-api`
- **Base image**: `python:3.12-slim`
- **Purpose**: FastAPI HTTP service (port 8001). Hybrid retrieval with:
  - **Dense search**: Embeds query with ONNX BERT, searches ChromaDB by cosine similarity.
  - **Sparse search**: BM25Okapi in-memory index over all embedded chunks.
  - **Fusion**: Reciprocal Rank Fusion (RRF) merging dense + sparse results.
  - **Reranking**: Optional BERT cross-encoder reranker + MMR diversity.
  - **Auth**: JWT-based authentication + API key validation + rate limiting.
  - BM25 index rebuilds periodically (default 300s) and immediately via Redis pub/sub when embedding-service marks documents ready.
  - Also serves document listing/stats endpoints and presigned S3 download URLs.
- **Build context**: `.` (project root)
- **Dockerfile**: `services/retrieval-api/Dockerfile`
- **System deps**: `gcc`, `curl`
- **Code copied**:
  - `shared/` → installed as `rag-shared`
  - `services/retrieval-api/requirements.txt` → pip install (`fastapi`, `uvicorn`, `onnxruntime`, `transformers`, `tokenizers`, `sentencepiece`, `numpy`, `rank-bm25`, `chromadb`, `PyJWT`)
  - `services/retrieval-api/app/` → `/app/app/` (contains `main.py`, `bm25_manager.py`, `schemas.py`, `schemas_documents.py`, `pipeline/`, `routers/`)
  - `services/retrieval-api/set-env.sh`
- **Volume**: `models_volume:/models:ro`
- **Port**: `18001 → 8001`
- **Depends on**: postgres, redis, chromadb (healthy), model-init (completed)
- **Healthcheck**: `curl -f http://localhost:8001/api/v1/health`
- **CMD**: `. /app/set-env.sh && exec uvicorn app.main:app --host 0.0.0.0 --port 8001`

### 18. `frontend`
- **Base image**: `nginx:1.25-alpine`
- **Purpose**: Serves the pre-compiled React SPA and reverse-proxies API requests:
  - `/api/ingest/*` → `ingest-api:8000/api/v1/*`
  - `/api/retrieval/*` → `retrieval-api:8001/api/v1/*`
  - `/api/auth/*` → `retrieval-api:8001/api/v1/auth/*`
  - All other paths → SPA fallback (`index.html`)
  - Uses Docker's embedded DNS resolver (`127.0.0.11`) so upstream hostnames are re-resolved on restart.
  - `client_max_body_size 500m` for PDF uploads.
- **Build context**: `./frontend-compiled`
- **Dockerfile**: `frontend-compiled/Dockerfile`
- **Code copied**:
  - `frontend-compiled/dist/` → `/usr/share/nginx/html` (pre-built React assets)
  - `frontend-compiled/nginx.conf` → `/etc/nginx/conf.d/default.conf`
- **Port**: `3001 → 80`
- **Depends on**: ingest-api (healthy), retrieval-api (healthy)

---

## Shared Library (`rag-shared`)

Every Python service installs `shared/` as the `rag-shared` pip package. It provides:

| Module | Contents |
|--------|----------|
| `config.py` | Pydantic `Settings` class, `REDIS_CHANNEL_BM25_REFRESH` constant |
| `auth/` | `api_key.py` (API key validation), `authenticator.py`, `jwt_handler.py` (JWT create/verify) |
| `cache/` | `redis_client.py` (async Redis factory), `embedding_cache.py` (Redis-backed embedding cache) |
| `db/` | `pool.py` (asyncpg pool), `chroma_client.py` (ChromaDB async client), `repositories/` (DocumentRepository, ChunkRepository, EmbeddingRepository) |
| `logging/` | `setup.py` (structlog configuration) |
| `metrics/` | `registry.py` (Prometheus counters/histograms for ONNX inference, batch duration, cache hit ratio, ingestion totals) |
| `onnx/` | `session_pool.py` (ONNX session pool with semaphore), `math_utils.py` (mean pooling, L2 normalize) |
| `queue/` | `connection.py` (RabbitMQ connection), `schemas.py` (IngestionTask, EmbeddingTask pydantic models), `topology.py` (exchange/queue declaration, routing key constants) |
| `storage/` | `s3_client.py` (async S3/MinIO upload, download, delete, presigned URLs) |
| `tracing/` | `otel.py` (OpenTelemetry tracer configuration, context injection/extraction) |

---

## Volumes

| Volume | Used by | Purpose |
|--------|---------|---------|
| `postgres_data` | postgres | Persistent database files |
| `redis_data` | redis | AOF + RDB persistence |
| `rabbitmq_data` | rabbitmq | Durable queues and messages |
| `minio_data` | minio | Uploaded PDF documents |
| `chromadb_data` | chromadb | Vector embeddings |
| `prometheus_data` | prometheus | Time-series metrics |
| `grafana_data` | grafana | Dashboard configs and state |
| `models_volume` | model-init (rw), embedding-service, ingestion-worker, retrieval-api (ro) | ONNX INT8 models + tokenizer files |

---

## Network

All containers share a single bridge network: `rag-network`.
