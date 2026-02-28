#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# ocr-service — environment variable defaults
#
# This file is sourced at container startup before the application launches.
# All variables use ${VAR:-default} so docker-compose / -e flags always win.
# ──────────────────────────────────────────────────────────────────────────────

# ── Message broker ────────────────────────────────────────────────────────────
export REDIS_URL=${REDIS_URL:-redis://localhost:6379/0}
export RABBITMQ_URL=${RABBITMQ_URL:-amqp://raguser:ragpassword123@localhost:5672/}

# ── Tesseract OCR ─────────────────────────────────────────────────────────────
# TESSDATA_PREFIX is also baked in as a Dockerfile ENV — this export makes the
# default explicit and overridable for local/non-Docker runs.
export TESSDATA_PREFIX=${TESSDATA_PREFIX:-/usr/share/tesseract-ocr/5/tessdata}
export OMP_THREAD_LIMIT=${OMP_THREAD_LIMIT:-2}
export OCR_LANGUAGES=${OCR_LANGUAGES:-eng}
export OCR_CONCURRENCY=${OCR_CONCURRENCY:-3}

# ── OCR backend selection ─────────────────────────────────────────────────────
# Set USE_OCR_API=true and configure OCR_API_URL to use an external OCR API
# (e.g. OpenAI Vision) instead of Tesseract.
export USE_OCR_API=${USE_OCR_API:-false}
export OCR_API_URL=${OCR_API_URL:-http://ocr-api:8002/ocr}
# Only required when USE_OCR_API=true and the API needs an OpenAI-style key
export OPENAI_API_KEY=${OPENAI_API_KEY:-}

# ── Observability (OpenTelemetry / Jaeger) ────────────────────────────────────
export JAEGER_ENDPOINT=${JAEGER_ENDPOINT:-http://jaeger:4317}
export OTEL_SERVICE_NAME=${OTEL_SERVICE_NAME:-ocr-service}
