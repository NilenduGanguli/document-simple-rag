#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# model-init — environment variable defaults
#
# This file is sourced at container startup before the one-time model
# download and ONNX export. All variables use ${VAR:-default} so
# docker-compose / -e flags always win.
#
# Models are loaded exclusively from S3 — no HuggingFace Hub access.
# ──────────────────────────────────────────────────────────────────────────────

# ── Model storage ─────────────────────────────────────────────────────────────
export MODEL_DEST=${MODEL_DEST:-/models}

# ── S3 model source ───────────────────────────────────────────────────────────
# Bucket defaults to S3_BUCKET (document bucket); override with MODEL_S3_BUCKET
export MODEL_S3_BUCKET=${MODEL_S3_BUCKET:-${S3_BUCKET:-rag-documents}}
export MODEL_S3_KEY_PREFIX=${MODEL_S3_KEY_PREFIX:-models/models/bert_uncased_L-12_H-768_A-12}

# ── Cache directories (derived from MODEL_DEST) ───────────────────────────────
export HF_HUB_CACHE=${HF_HUB_CACHE:-${MODEL_DEST}/_hf_cache}
export HF_HOME=${HF_HOME:-${MODEL_DEST}/_hf_cache}
export TRANSFORMERS_CACHE=${TRANSFORMERS_CACHE:-${MODEL_DEST}/_hf_cache}

# ── Offline mode — always enforced, no internet downloads allowed ──────────────
export TRANSFORMERS_OFFLINE=1
export HF_HUB_OFFLINE=1

# ── Re-initialisation control ─────────────────────────────────────────────────
# Set FORCE_REINIT=true to re-download from S3 and re-export even if models exist
export FORCE_REINIT=${FORCE_REINIT:-false}
