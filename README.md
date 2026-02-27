# Enterprise RAG Pipeline

A production-ready, CPU-only Retrieval-Augmented Generation (RAG) pipeline that processes
documents end-to-end: ingestion → OCR → chunking → embedding → hybrid retrieval.

Start the full stack with a single command:

```bash
make build && make up
```

## Architecture Overview

```
             ┌──────────────┐
 client ───▶ │  Ingest API  │  POST /api/v1/documents
             │   :18000     │───▶ S3/MinIO ───▶ RabbitMQ (rag.ingestion)
             └──────────────┘

             ┌──────────────────────┐
             │  Ingestion Worker x6 │  PDF→ text/images, chunking
             │                      │───▶ RabbitMQ (rag.ocr) ──▶ OCR Service
             └──────────────────────┘         (Tesseract CPU)

             ┌──────────────────┐
             │ Embedding Service │  ONNX BERT INT8 CPU inference
             │                  │  tokenise → ONNX session pool → PGVector
             └──────────────────┘

             ┌──────────────────┐
 client ───▶ │  Retrieval API   │  POST /api/v1/retrieve
             │   :18001         │  dense HNSW + BM25 → RRF → MMR → rerank
             └──────────────────┘
```

### Services

| Service               | Port  | Description                                              |
| --------------------- | ----- | -------------------------------------------------------- |
| `ingest-api`        | 18000 | FastAPI: document upload, status polling, delete         |
| `ingestion-worker`  | —    | Async worker: PyMuPDF routing, OCR dispatch, chunking    |
| `ocr-service`       | —    | Tesseract RPC service for image-only PDF pages           |
| `ocr-api`           | 8002  | LLM OCR via GPT-4o-mini (optional, set USE_OCR_API=true) |
| `embedding-service` | 8080  | ONNX BERT INT8: batched embedding → PGVector            |
| `retrieval-api`     | 18001 | Hybrid search: HNSW + BM25 + RRF + MMR + cross-encoder   |
| `model-init`        | —    | One-shot: downloads BERT, exports ONNX FP32 → INT8      |

### Infrastructure

| Service        | Port(s)       | Description                                                |
| -------------- | ------------- | ---------------------------------------------------------- |
| `postgres`   | 15432         | PostgreSQL 16 + pgvector extension                         |
| `redis`      | 16379         | Cache layer (embedding, dedup, query results, rate limits) |
| `rabbitmq`   | 5672 / 15672  | Message broker (ingestion, embedding, OCR queues)          |
| `minio`      | 19000 / 19001 | S3-compatible object storage for raw documents             |
| `prometheus` | 9090          | Metrics collection                                         |
| `grafana`    | 3000          | Dashboards                                                 |
| `jaeger`     | 16686 / 4317  | Distributed tracing (OTLP gRPC)                            |

## Quick Start

### Prerequisites

- Docker Engine 24+ with Compose v2
- 8 GB RAM minimum (16 GB recommended for model download + inference)
- 10 GB free disk space (BERT model ~1.4 GB, images ~2 GB)

### Setup

```bash
# 1. Clone / enter the repository
cd document-simple-rag

# 2. Copy environment file (edit passwords before production use)
cp .env.example .env

# 3. Build images (first build downloads base images and compiles packages)
make build

# 4. Start the full stack
make up

# 5. Follow startup logs
make logs
```

The `model-init` container runs first and downloads `bert-base-multilingual-cased`
from HuggingFace, exports it to ONNX FP32, and quantizes to INT8. This happens once
and the result is cached in the `models_volume` Docker volume.

### Verify the stack is healthy

```bash
make test-health
```

Expected output:

```json
{"status": "ok", "service": "ingest-api"}
{"status": "ok", "service": "retrieval-api"}
```

### Web UIs

| Interface           | URL                                | Credentials              |
| ------------------- | ---------------------------------- | ------------------------ |
| Ingest API docs     | http://localhost:18000/docs        | —                       |
| Retrieval API docs  | http://localhost:18001/api/v1/docs | —                       |
| RabbitMQ Management | http://localhost:15672             | raguser / (from .env)    |
| MinIO Console       | http://localhost:19001             | minioadmin / (from .env) |
| Prometheus          | http://localhost:9090              | —                       |
| Grafana             | http://localhost:3000              | admin / (from .env)      |
| Jaeger              | http://localhost:16686             | —                       |

## Usage

### Upload a document

```bash
curl -X POST http://localhost:18000/api/v1/documents \
  -H "X-API-Key: dev-api-key-1" \
  -F "file=@/path/to/document.pdf"
```

Response:

```json
{
  "parent_document_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "pending",
  "filename": "document.pdf"
}
```

### Poll document status

```bash
curl http://localhost:18000/api/v1/documents/3fa85f64-5717-4562-b3fc-2c963f66afa6 \
  -H "X-API-Key: dev-api-key-1"
```

Possible statuses: `pending` → `ingesting` → `chunking` → `embedding` → `ready` | `failed`

### Retrieve relevant chunks

```bash
curl -X POST http://localhost:18001/api/v1/retrieve \
  -H "X-API-Key: dev-api-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is the capital of France?",
    "config": {
      "k": 5,
      "retrieval_mode": "k_chunks",
      "use_reranker": true
    }
  }'
```

### Delete a document

```bash
curl -X DELETE http://localhost:18000/api/v1/documents/3fa85f64-5717-4562-b3fc-2c963f66afa6 \
  -H "X-API-Key: dev-api-key-1"
```

## Configuration

All configuration is driven by environment variables in `.env`:

| Variable                | Default                         | Description                      |
| ----------------------- | ------------------------------- | -------------------------------- |
| `POSTGRES_PASSWORD`   | `ragpassword123`              | PostgreSQL password              |
| `RABBITMQ_USER`       | `raguser`                     | RabbitMQ username                |
| `RABBITMQ_PASS`       | `ragpassword123`              | RabbitMQ password                |
| `MINIO_ROOT_USER`     | `minioadmin`                  | MinIO root user                  |
| `MINIO_ROOT_PASSWORD` | `minioadmin123`               | MinIO root password              |
| `GRAFANA_PASSWORD`    | `admin`                       | Grafana admin password           |
| `API_KEYS`            | `dev-api-key-1,dev-api-key-2` | Comma-separated valid API keys   |
| `FORCE_MODEL_REINIT`  | `false`                       | Force re-download of ONNX models |

## Development

### View logs for a specific service

```bash
docker compose logs -f embedding-service
docker compose logs -f retrieval-api
```

### Open a database shell

```bash
make shell-postgres
```

### Inspect Redis

```bash
make shell-redis
# Then: KEYS emb:*
```

### Force model re-download

```bash
make model-reinit
```

### Stop everything and clean volumes

```bash
make down-volumes
```

## Project Structure

```
.
├── docker-compose.yml           # Full stack definition
├── .env.example                 # Environment template
├── Makefile                     # Developer convenience targets
├── infra/
│   ├── postgres/01_init.sql    # DB schema (pgvector, 4 tables)
│   ├── rabbitmq/               # rabbitmq.conf + definitions.json
│   ├── redis/redis.conf
│   ├── minio/init.sh           # Bucket creation script
│   ├── prometheus/prometheus.yml
│   └── grafana/provisioning/   # Datasource + dashboard provisioning
├── shared/                      # rag-shared: installable Python package
│   └── rag_shared/
│       ├── auth/               # API key validation, rate limiting
│       ├── cache/              # Embedding cache, Redis client
│       ├── db/                 # asyncpg pool, repositories
│       ├── logging/            # structlog JSON setup
│       ├── metrics/            # 9 Prometheus metrics
│       ├── onnx/               # ONNXSessionPool, pooling math utils
│       ├── queue/              # RabbitMQ topology, schemas, connection
│       ├── storage/            # aioboto3 S3 client
│       └── tracing/            # OpenTelemetry OTLP setup
└── services/
    ├── model-init/             # One-shot BERT download + ONNX export
    ├── ingest-api/             # FastAPI document ingestion
    ├── ingestion-worker/       # PDF routing + chunking worker
    ├── ocr-service/            # Tesseract RPC worker
    ├── embedding-service/      # ONNX embedding + PGVector insert
    └── retrieval-api/          # Hybrid retrieval (HNSW+BM25+RRF+MMR)
```

## Retrieval Pipeline

The 11-stage retrieval pipeline:

1. **Rate limit check** — sliding window per API key (Redis)
2. **Result cache** — exact query cache (Redis, 5 min TTL)
3. **Query preprocessing** — NER entity extraction, query normalisation
4. **Query embedding** — ONNX BERT INT8 biencoder session pool
5. **Dense search** — pgvector HNSW cosine (ef_search=200), top-3k candidates
6. **Sparse search** — BM25 Okapi over all chunk texts, top-3k candidates
7. **RRF fusion** — Reciprocal Rank Fusion (k=60) combining both lists
8. **MMR reranking** — Maximum Marginal Relevance for diversity
9. **Cross-encoder reranking** — ONNX BERT INT8 cross-encoder for final scoring
10. **Result assembly** — n_documents or k_chunks mode aggregation
11. **Audit log** — async background insert to `retrieval_audit` table

## Monitoring

### Prometheus Metrics

| Metric                               | Type      | Description                   |
| ------------------------------------ | --------- | ----------------------------- |
| `rag_ingest_documents_total`       | Counter   | Documents ingested by status  |
| `rag_queue_depth`                  | Gauge     | Current RabbitMQ queue depths |
| `rag_retrieval_latency_ms`         | Histogram | End-to-end retrieval latency  |
| `rag_onnx_inference_duration_ms`   | Histogram | ONNX session inference time   |
| `rag_onnx_pool_wait_ms`            | Histogram | Time waiting for pool session |
| `rag_embedding_batch_duration_ms`  | Histogram | Embedding batch processing    |
| `rag_cache_hit_ratio`              | Gauge     | Embedding cache hit ratio     |
| `rag_pgvector_search_ms`           | Histogram | PGVector HNSW search latency  |
| `rag_ocp_pod_cpu_throttling_ratio` | Gauge     | CPU throttling ratio          |

### Distributed Tracing

All services export OpenTelemetry traces to Jaeger via OTLP gRPC (port 4317).
View traces at http://localhost:16686.

## Design Notes

- **CPU-only inference**: No GPU required. BERT runs as ONNX INT8 quantized model
  via `onnxruntime` CPU provider with session pools (`ONNX_POOL_SIZE`, `ONNX_THREADS_PER_SESSION`).
- **torch isolation**: PyTorch is only in the `model-init` image for one-time ONNX export.
  Runtime services use only `onnxruntime` keeping images lean (~300 MB vs ~3 GB).
- **Exactly-once semantics**: RabbitMQ quorum queues with `x-delivery-limit=5` and
  dead-letter exchanges prevent message loss and unbounded redelivery.
- **SHA-256 deduplication**: Duplicate document uploads are detected via Redis
  (TTL 7 days) before any S3 write.
- **BM25 refresh**: Background task atomically refreshes the in-memory BM25 index
  every 5 minutes as new chunks arrive.
