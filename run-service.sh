#!/usr/bin/env bash
# run-service.sh — Build and start the Enterprise RAG Pipeline stack.
#
# Usage:
#   ./run-service.sh                  # build + start full stack (~18 containers)
#   ./run-service.sh --build          # build only, do not start
#   ./run-service.sh --start          # start only (skip build)
#   ./run-service.sh --stop           # stop all containers
#   ./run-service.sh --clean          # stop containers and remove volumes
#
#   ./run-service.sh --dev            # build + start 4-container dev stack
#   ./run-service.sh --dev --build    # build dev images only
#   ./run-service.sh --dev --start    # start dev stack only (skip build)
#   ./run-service.sh --dev --stop     # stop dev stack
#   ./run-service.sh --dev --clean    # stop dev stack and remove volumes
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
section() { echo -e "${CYAN}[====]${NC}  $*"; }

# ─── Parse args ──────────────────────────────────────────────────────────────
DEV_MODE=false
DO_BUILD=true
DO_START=true
ACTION=""   # stop | clean

for arg in "$@"; do
  case "$arg" in
    --dev)   DEV_MODE=true ;;
    --build) DO_START=false ;;
    --start) DO_BUILD=false ;;
    --stop)  ACTION=stop ;;
    --clean) ACTION=clean ;;
    --help|-h)
      sed -n '/^# Usage:/,/^#$/p' "$0" | sed 's/^# \{0,3\}//'
      exit 0
      ;;
    *)
      error "Unknown option: $arg\nRun $0 --help for usage."
      ;;
  esac
done

# ─── Select compose file ─────────────────────────────────────────────────────
if $DEV_MODE; then
  COMPOSE_FILE="docker-compose.4container.yml"
  STACK_LABEL="4-container dev stack"
else
  COMPOSE_FILE="docker-compose.yml"
  STACK_LABEL="full stack"
fi

DC="docker compose -f ${COMPOSE_FILE} "

# ─── Pre-flight checks ───────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || error "Docker is not installed or not in PATH."
docker compose version >/dev/null 2>&1 || error "Docker Compose v2 is required (run: docker compose version)."

[ -f "$COMPOSE_FILE" ] || error "Compose file not found: ${COMPOSE_FILE}"

# ─── Stop ────────────────────────────────────────────────────────────────────
if [ "$ACTION" = "stop" ]; then
  info "Stopping ${STACK_LABEL}..."
  $DC down
  info "Done."
  exit 0
fi

# ─── Clean ───────────────────────────────────────────────────────────────────
if [ "$ACTION" = "clean" ]; then
  warn "Removing ${STACK_LABEL} containers and all named volumes (data will be lost)..."
  $DC down -v
  info "Done."
  exit 0
fi

# ─── Environment file ────────────────────────────────────────────────────────
if [ -f .env ]; then
  info "Found .env — values in it will override compose defaults."
fi

# ─── Build ───────────────────────────────────────────────────────────────────
if $DO_BUILD; then
  section "Building ${STACK_LABEL} images (may take several minutes on first run)..."
  $DC build --parallel
  info "Build complete."
fi

# ─── Start ───────────────────────────────────────────────────────────────────
if $DO_START; then
  section "Starting ${STACK_LABEL}..."
  $DC up -d

  echo ""
  info "Service endpoints:"
  echo "  Frontend       → http://localhost:3001"
  echo "  Ingest API     → http://localhost:18000/docs"
  echo "  Retrieval API  → http://localhost:18001/docs"

  if $DEV_MODE; then
    echo "  RabbitMQ UI    → http://localhost:15672"
    echo "  MinIO Console  → http://localhost:19001"
    echo ""
    info "Follow logs:  docker compose -f ${COMPOSE_FILE} logs -f"
    info "Stop:         $0 --dev --stop"
  else
    echo "  OCR API        → http://localhost:8002/docs"
    echo "  RabbitMQ UI    → http://localhost:15672"
    echo "  MinIO Console  → http://localhost:19001"
    echo "  Prometheus     → http://localhost:9090"
    echo "  Grafana        → http://localhost:3000"
    echo "  Jaeger UI      → http://localhost:16686"
    echo ""
    info "Follow logs:  docker compose logs -f"
    info "Stop:         $0 --stop"
  fi
fi
