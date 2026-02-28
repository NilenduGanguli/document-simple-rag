#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# model-init — environment variable defaults
#
# This file is sourced at container startup before the one-time model download
# and ONNX export. All variables use ${VAR:-default} so docker-compose /
# -e flags always win.
# ──────────────────────────────────────────────────────────────────────────────

# ── Model storage ─────────────────────────────────────────────────────────────
export MODEL_DEST=${MODEL_DEST:-/models}

# ── HuggingFace model selection ───────────────────────────────────────────────
export HF_MODEL_NAME=${HF_MODEL_NAME:-bert-base-multilingual-cased}

# ── Cache directories (derived from MODEL_DEST by default) ───────────────────
export HF_HUB_CACHE=${HF_HUB_CACHE:-${MODEL_DEST}/_hf_cache}
export HF_HOME=${HF_HOME:-${MODEL_DEST}/_hf_cache}
export TRANSFORMERS_CACHE=${TRANSFORMERS_CACHE:-${MODEL_DEST}/_hf_cache}

# ── Optional HuggingFace auth token (needed for private/gated models) ─────────
export HF_TOKEN=${HF_TOKEN:-}

# ── Local model path (optional — skips HuggingFace download entirely) ─────────
# Set to an absolute path inside the container to load a TF checkpoint from disk
export LOCAL_MODEL_PATH=${LOCAL_MODEL_PATH:-}

# ── Offline mode ──────────────────────────────────────────────────────────────
# When LOCAL_MODEL_PATH is set these should be 1; default 0 allows HF download
export TRANSFORMERS_OFFLINE=${TRANSFORMERS_OFFLINE:-0}
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-0}

# ── Re-initialisation control ─────────────────────────────────────────────────
# Set FORCE_REINIT=true to re-download and re-export even if models exist
export FORCE_REINIT=${FORCE_REINIT:-false}
