#!/bin/sh
set -e

# ---------------------------------------------------------------------------
# Model initialization (one-shot: skipped if /models/.ready already exists)
# ---------------------------------------------------------------------------
MODELS_READY="${MODEL_DEST:-/models}/.ready"

if [ ! -f "${MODELS_READY}" ] || [ "${FORCE_REINIT:-false}" = "true" ]; then
    echo "[backend] models not found — running model-init..."
    . /app/model_init/set-env.sh
    python /app/model_init/model_init.py
    echo "[backend] model-init complete."
else
    echo "[backend] models already initialized (${MODELS_READY}), skipping."
fi

# ---------------------------------------------------------------------------
# Start unified FastAPI server
# ---------------------------------------------------------------------------
echo "[backend] starting unified FastAPI server on port 8000..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info
