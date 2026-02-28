#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# embedding-service — environment variable defaults
#
# This file is sourced at container startup before the application launches.
# All variables use ${VAR:-default} so docker-compose / -e flags always win.
# ──────────────────────────────────────────────────────────────────────────────

# ── Database ──────────────────────────────────────────────────────────────────
export DATABASE_URL=${DATABASE_URL:-postgresql://raguser:ragpassword123@localhost:5432/ragdb}

# ── Message broker (Redis DB 1 — isolated from ingest) ───────────────────────
export REDIS_URL=${REDIS_URL:-redis://localhost:6379/1}
export RABBITMQ_URL=${RABBITMQ_URL:-amqp://raguser:ragpassword123@localhost:5672/}

# ── ONNX model runtime ────────────────────────────────────────────────────────
export MODEL_DEST=${MODEL_DEST:-/models}
export MODEL_VERSION=${MODEL_VERSION:-local-docker-compose}
export ONNX_POOL_SIZE=${ONNX_POOL_SIZE:-2}
export ONNX_THREADS_PER_SESSION=${ONNX_THREADS_PER_SESSION:-2}
export EMBEDDING_MODEL_NAME=${EMBEDDING_MODEL_NAME:-bert-base-uncased-int8}

# ── Offline mode (models loaded from /models volume, not HuggingFace) ─────────
export TRANSFORMERS_OFFLINE=${TRANSFORMERS_OFFLINE:-1}
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}

# ── Batching ──────────────────────────────────────────────────────────────────
export EMBEDDING_BATCH_SIZE=${EMBEDDING_BATCH_SIZE:-16}
export PREFETCH_QUEUE_SIZE=${PREFETCH_QUEUE_SIZE:-4}
export BATCH_COLLECT_TIMEOUT=${BATCH_COLLECT_TIMEOUT:-0.1}

# ── Threading / BLAS ──────────────────────────────────────────────────────────
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-2}
export OMP_WAIT_POLICY=${OMP_WAIT_POLICY:-PASSIVE}
export MKL_NUM_THREADS=${MKL_NUM_THREADS:-2}
export GOMP_SPINCOUNT=${GOMP_SPINCOUNT:-0}
export TOKENIZERS_PARALLELISM=${TOKENIZERS_PARALLELISM:-false}

# ── Logging ───────────────────────────────────────────────────────────────────
export LOG_LEVEL=${LOG_LEVEL:-info}

# ── Observability (OpenTelemetry / Jaeger) ────────────────────────────────────
export JAEGER_ENDPOINT=${JAEGER_ENDPOINT:-http://jaeger:4317}
export OTEL_SERVICE_NAME=${OTEL_SERVICE_NAME:-embedding-service}
