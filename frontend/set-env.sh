#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# frontend — build-time environment variable defaults
#
# This file is sourced inside the Node.js build stage before `npm run build`.
# Vite bakes VITE_* variables into the static bundle at build time — they are
# NOT available at nginx runtime.
#
# Override via docker-compose build args or `docker build --build-arg`.
# ──────────────────────────────────────────────────────────────────────────────

# ── API key pre-loaded into the browser localStorage on first visit ───────────
# The UI lets users change this value at any time via the header control.
export VITE_DEFAULT_API_KEY=${VITE_DEFAULT_API_KEY:-dev-api-key-1}
