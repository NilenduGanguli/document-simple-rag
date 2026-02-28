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

# ── OCR concurrency ──────────────────────────────────────────────────────────
export OCR_CONCURRENCY=${OCR_CONCURRENCY:-3}

# ── OCR API ───────────────────────────────────────────────────────────────────
export OCR_API_URL=${OCR_API_URL:-http://ocr-api:8002/ocr}

# ── Observability (OpenTelemetry / Jaeger) ────────────────────────────────────
export JAEGER_ENDPOINT=${JAEGER_ENDPOINT:-http://jaeger:4317}
export OTEL_SERVICE_NAME=${OTEL_SERVICE_NAME:-ocr-service}
