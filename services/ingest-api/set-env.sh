#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# ingest-api — environment variable defaults
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

# ── Security ──────────────────────────────────────────────────────────────────
export API_KEYS=${API_KEYS:-dev-api-key-1}

# ── Rate limiting ─────────────────────────────────────────────────────────────
export RATE_LIMIT_PER_MINUTE=${RATE_LIMIT_PER_MINUTE:-1000}
export RATE_LIMIT_PER_IP=${RATE_LIMIT_PER_IP:-50}

# ── Observability (OpenTelemetry / Jaeger) ────────────────────────────────────
export JAEGER_ENDPOINT=${JAEGER_ENDPOINT:-http://jaeger:4317}
export OTEL_SERVICE_NAME=${OTEL_SERVICE_NAME:-ingest-api}
