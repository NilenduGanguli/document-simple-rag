#!/usr/bin/env bash
# run-service.sh — Build and start the Enterprise RAG Pipeline stack.
#
# Usage:
#   ./run-service.sh            # build all images then start the full stack
#   ./run-service.sh --build    # build only, do not start
#   ./run-service.sh --start    # start only (skip build)
#   ./run-service.sh --stop     # stop all containers
#   ./run-service.sh --clean    # stop containers and remove volumes
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ─── Parse args ──────────────────────────────────────────────────────────────
DO_BUILD=true
DO_START=true

case "${1:-}" in
  --build) DO_START=false ;;
  --start) DO_BUILD=false ;;
  --stop)
    info "Stopping all containers..."
    docker compose down
    info "Done."
    exit 0
    ;;
  --clean)
    warn "Removing containers and all named volumes (data will be lost)..."
    docker compose down -v
    info "Done."
    exit 0
    ;;
  ""|--help)
    # defaults: build + start
    ;;
  *)
    error "Unknown option: $1\nUsage: $0 [--build|--start|--stop|--clean]"
    ;;
esac

# ─── Pre-flight checks ───────────────────────────────────────────────────────
command -v docker   >/dev/null 2>&1 || error "Docker is not installed or not in PATH."
docker compose version >/dev/null 2>&1 || error "Docker Compose v2 is required (run: docker compose version)."

# ─── Environment file ────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    warn ".env not found — copying from .env.example"
    cp .env.example .env
    warn "Review .env and set any required secrets (OPENAI_API_KEY if USE_OCR_API=true, etc.) before restarting."
  else
    error ".env file not found and no .env.example to copy from."
  fi
fi

# Warn if USE_OCR_API=true but OPENAI_API_KEY is missing / commented out
if grep -qE '^USE_OCR_API=true' .env 2>/dev/null; then
  if ! grep -qE '^OPENAI_API_KEY=.+' .env 2>/dev/null; then
    warn "USE_OCR_API=true but OPENAI_API_KEY is not set in .env — ocr-api will fail at runtime."
  fi
fi

# ─── Build ────────────────────────────────────────────────────────────────────
if $DO_BUILD; then
  info "Building Docker images (this may take a few minutes on the first run)..."
  docker compose build --parallel
  info "Build complete."
fi

# ─── Start ────────────────────────────────────────────────────────────────────
if $DO_START; then
  info "Starting the full stack..."
  docker compose up -d

  echo ""
  info "Stack started. Service endpoints:"
  echo "  Frontend       → http://localhost:3001"
  echo "  Ingest API     → http://localhost:18000/docs"
  echo "  Retrieval API  → http://localhost:18001/docs"
  echo "  OCR API        → http://localhost:8002/docs"
  echo "  RabbitMQ UI    → http://localhost:15672"
  echo "  MinIO Console  → http://localhost:19001"
  echo "  Prometheus     → http://localhost:9090"
  echo "  Grafana        → http://localhost:3000"
  echo "  Jaeger UI      → http://localhost:16686"
  echo "  Frontend UI   → http://localhost:3001"
  echo ""
  info "Follow logs with: docker compose logs -f"
  info "Stop with:        ./run-service.sh --stop"
fi
