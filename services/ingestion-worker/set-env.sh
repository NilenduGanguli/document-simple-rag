#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# ingestion-worker — environment variable defaults
#
# This file is sourced at container startup before the application launches.
# All variables use ${VAR:-default} so docker-compose / -e flags always win.
# ──────────────────────────────────────────────────────────────────────────────

# ── Database ──────────────────────────────────────────────────────────────────
export DATABASE_URL=${DATABASE_URL:-postgresql://raguser:ragpassword123@localhost:5432/ragdb}

# ── Message broker ────────────────────────────────────────────────────────────
export REDIS_URL=${REDIS_URL:-redis://localhost:6379/0}
export RABBITMQ_URL=${RABBITMQ_URL:-amqp://raguser:ragpassword123@localhost:5672/}

# ── Object storage (MinIO / S3) ───────────────────────────────────────────────
export S3_ENDPOINT_URL=${S3_ENDPOINT_URL:-http://minio:9000}
export S3_ACCESS_KEY=${S3_ACCESS_KEY:-minioadmin}
export S3_SECRET_KEY=${S3_SECRET_KEY:-minioadmin123}
export S3_BUCKET=${S3_BUCKET:-rag-documents}
export S3_REGION=${S3_REGION:-us-east-1}

# ── Ingestion limits ──────────────────────────────────────────────────────────
export MAX_FILE_SIZE_MB=${MAX_FILE_SIZE_MB:-500}

# ── Worker concurrency ────────────────────────────────────────────────────────
export WORKER_CONCURRENCY=${WORKER_CONCURRENCY:-6}
export OCR_CONCURRENCY_LIMIT=${OCR_CONCURRENCY_LIMIT:-3}

# ── Chunking strategy ─────────────────────────────────────────────────────────
export CHUNKING_STRATEGY=${CHUNKING_STRATEGY:-recursive}
export CHUNK_MAX_TOKENS=${CHUNK_MAX_TOKENS:-400}
export CHUNK_OVERLAP_TOKENS=${CHUNK_OVERLAP_TOKENS:-50}

# ── Tokenizer / model ─────────────────────────────────────────────────────────
export TOKENIZER_MODEL=${TOKENIZER_MODEL:-/models/embedding/int8}
export TOKENIZERS_PARALLELISM=${TOKENIZERS_PARALLELISM:-false}

# ── Offline mode (models loaded from /models volume, not HuggingFace) ─────────
export TRANSFORMERS_OFFLINE=${TRANSFORMERS_OFFLINE:-1}
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}

# ── Observability (OpenTelemetry / Jaeger) ────────────────────────────────────
export JAEGER_ENDPOINT=${JAEGER_ENDPOINT:-http://jaeger:4317}
export OTEL_SERVICE_NAME=${OTEL_SERVICE_NAME:-ingestion-worker}
