#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# retrieval-api — environment variable defaults
#
# This file is sourced at container startup before the application launches.
# All variables use ${VAR:-default} so docker-compose / -e flags always win.
# ──────────────────────────────────────────────────────────────────────────────

# ── Database ──────────────────────────────────────────────────────────────────
export DATABASE_URL=${DATABASE_URL:-postgresql://raguser:ragpassword123@localhost:5432/ragdb}

# ── Cache (Redis DB 2 — isolated from ingest/embedding) ──────────────────────
export REDIS_URL=${REDIS_URL:-redis://localhost:6379/2}

# ── Object storage (MinIO / S3) ───────────────────────────────────────────────
export S3_ENDPOINT_URL=${S3_ENDPOINT_URL:-http://minio:9000}
export S3_ACCESS_KEY=${S3_ACCESS_KEY:-minioadmin}
export S3_SECRET_KEY=${S3_SECRET_KEY:-minioadmin123}
export S3_BUCKET=${S3_BUCKET:-rag-documents}
export S3_REGION=${S3_REGION:-us-east-1}
# Browser-accessible URL for presigned download URLs (empty = same as S3_ENDPOINT_URL)
export S3_EXTERNAL_URL=${S3_EXTERNAL_URL:-http://localhost:19000}

# ── ONNX model runtime ────────────────────────────────────────────────────────
export MODEL_DEST=${MODEL_DEST:-/models}
export MODEL_VERSION=${MODEL_VERSION:-local-docker-compose}
export ONNX_POOL_SIZE=${ONNX_POOL_SIZE:-1}
export ONNX_THREADS_PER_SESSION=${ONNX_THREADS_PER_SESSION:-2}
export TOKENIZERS_PARALLELISM=${TOKENIZERS_PARALLELISM:-false}

# ── Offline mode (models loaded from /models volume, not HuggingFace) ─────────
export TRANSFORMERS_OFFLINE=${TRANSFORMERS_OFFLINE:-1}
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}

# ── BM25 index ────────────────────────────────────────────────────────────────
export BM25_REFRESH_INTERVAL_SECONDS=${BM25_REFRESH_INTERVAL_SECONDS:-300}

# ── Query limits ──────────────────────────────────────────────────────────────
export MAX_QUERY_TOKENS=${MAX_QUERY_TOKENS:-100}

# ── Security ──────────────────────────────────────────────────────────────────
export API_KEYS=${API_KEYS:-dev-api-key-1}

# ── Rate limiting ─────────────────────────────────────────────────────────────
export RATE_LIMIT_PER_MINUTE=${RATE_LIMIT_PER_MINUTE:-1000}
export RATE_LIMIT_PER_IP=${RATE_LIMIT_PER_IP:-50}

# ── CORS ──────────────────────────────────────────────────────────────────────
export CORS_ORIGINS=${CORS_ORIGINS:-*}

# ── Observability (OpenTelemetry / Jaeger) ────────────────────────────────────
export JAEGER_ENDPOINT=${JAEGER_ENDPOINT:-http://jaeger:4317}
export OTEL_SERVICE_NAME=${OTEL_SERVICE_NAME:-retrieval-api}

# ── Environment ────────────────────────────────────────────────────────────────
export ENVIRONMENT=${ENVIRONMENT:-DEV}

# ── JWT ────────────────────────────────────────────────────────────────────────
export JWT_SECRET=${JWT_SECRET:-dev-jwt-secret-change-in-production}
export JWT_EXPIRY_HOURS=${JWT_EXPIRY_HOURS:-8}
