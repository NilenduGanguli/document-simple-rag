#!/bin/sh
set -e

# Models are loaded from S3 only — block all HuggingFace Hub access.
export TRANSFORMERS_OFFLINE=1
export HF_HUB_OFFLINE=1

# ---------------------------------------------------------------------------
# Model initialization (one-shot: skipped if /models/.ready already exists)
# ---------------------------------------------------------------------------
MODELS_READY="${MODEL_DEST:-/models}/.ready"

if [ ! -f "${MODELS_READY}" ] || [ "${FORCE_REINIT:-false}" = "true" ]; then
    echo "[backend] models not found — running model-init (S3 source)..."
    . /app/model_init/set-env.sh
    python /app/model_init/model_init.py
    echo "[backend] model-init complete."
else
    echo "[backend] models already initialized (${MODELS_READY}), skipping."
fi

# ---------------------------------------------------------------------------
# Database bootstrap (idempotent — safe on every restart)
# Ensures extensions (uuid-ossp, vector) and schema objects exist.
# Set DATABASE_ADMIN_URL to a superuser DSN when using a managed database
# (RDS, Cloud SQL, etc.) that requires elevated privileges to CREATE EXTENSION.
# ---------------------------------------------------------------------------
echo "[backend] bootstrapping database schema..."
python /app/db_ensure.py
echo "[backend] database bootstrap done."

# ---------------------------------------------------------------------------
# Start unified FastAPI server
# ---------------------------------------------------------------------------
echo "[backend] starting unified FastAPI server on port 8000..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info
