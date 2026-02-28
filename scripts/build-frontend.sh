#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# build-frontend.sh
#
# Builds the frontend from source (frontend/) and copies the compiled output
# into frontend-compiled/ so docker-compose can build a lightweight nginx
# image without needing Node.js.
#
# Usage:
#   ./scripts/build-frontend.sh            # build + sync
#   ./scripts/build-frontend.sh --docker   # also rebuild & restart the container
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$REPO_ROOT/frontend"
OUT_DIR="$REPO_ROOT/frontend-compiled"

# ── 1. Install dependencies (if needed) ─────────────────────────────────────
echo "==> Installing frontend dependencies…"
cd "$SRC_DIR"
npm ci --silent

# ── 2. Source build-time env vars ────────────────────────────────────────────
if [ -f "$SRC_DIR/set-env.sh" ]; then
  echo "==> Sourcing set-env.sh…"
  # shellcheck disable=SC1091
  . "$SRC_DIR/set-env.sh"
fi

# ── 3. Build ─────────────────────────────────────────────────────────────────
echo "==> Building frontend (tsc + vite)…"
npm run build

# ── 4. Sync to frontend-compiled/ ───────────────────────────────────────────
echo "==> Syncing dist → $OUT_DIR/"
mkdir -p "$OUT_DIR"

# Remove stale dist and replace with fresh build
rm -rf "$OUT_DIR/dist"
cp -r "$SRC_DIR/dist" "$OUT_DIR/dist"

# Copy nginx config (always keep in sync with source)
cp "$SRC_DIR/nginx.conf" "$OUT_DIR/nginx.conf"

# Ensure Dockerfile exists (create only if missing)
if [ ! -f "$OUT_DIR/Dockerfile" ]; then
  cat > "$OUT_DIR/Dockerfile" <<'DOCKERFILE'
# Pre-compiled frontend — no Node.js build stage required.
# Uses the pre-built dist/ output directly with nginx.
FROM nginx:1.25-alpine
COPY dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
DOCKERFILE
  echo "    Created Dockerfile"
fi

echo "==> frontend-compiled/ ready:"
ls -lh "$OUT_DIR/dist/"

# ── 5. (Optional) Rebuild & restart Docker container ─────────────────────────
if [ "${1:-}" = "--docker" ]; then
  echo "==> Rebuilding and restarting frontend container…"
  cd "$REPO_ROOT"
  docker compose build frontend
  docker compose up -d frontend
  echo "==> Frontend container restarted."
fi

echo "==> Done."
