#!/usr/bin/env bash
# reingest_sec_fillings.sh — Upload all PDFs from sec_fillings/ to the ingest API.
#
# Usage:
#   ./scripts/reingest_sec_fillings.sh <API_KEY>
#   API_KEY=my-key ./scripts/reingest_sec_fillings.sh
#
# The API key is read from:
#   1. First positional argument ($1)
#   2. API_KEY environment variable
#   3. Defaults to dev-api-key-1 (matches docker-compose.yml default)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SEC_DIR="$REPO_ROOT/sec_fillings"
INGEST_URL="${INGEST_URL:-http://localhost:18000}/api/v1/documents/ingest"

# ── Resolve API key ───────────────────────────────────────────────────────────
API_KEY="${1:-${API_KEY:-dev-api-key-1}}"

# ── Wait for ingest-api health ────────────────────────────────────────────────
HEALTH_URL="${INGEST_URL%/api/v1/documents/ingest}/api/v1/health"
echo "Waiting for ingest-api at $HEALTH_URL ..."
for i in $(seq 1 30); do
  if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Ingest API is up."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "Error: ingest-api did not become healthy after 30 attempts."
    exit 1
  fi
  sleep 2
done

# ── Upload each PDF ───────────────────────────────────────────────────────────
shopt -s nullglob
pdfs=("$SEC_DIR"/*.pdf)
if [ ${#pdfs[@]} -eq 0 ]; then
  echo "No PDF files found in $SEC_DIR"
  exit 1
fi

echo ""
echo "Uploading ${#pdfs[@]} PDF(s) from $SEC_DIR ..."
echo ""

for pdf in "${pdfs[@]}"; do
  name="$(basename "$pdf")"
  echo "  → $name"
  response=$(curl -sf -X POST "$INGEST_URL" \
    -H "X-API-Key: $API_KEY" \
    -F "file=@${pdf};type=application/pdf" 2>&1) || {
      echo "    [FAILED] $response"
      continue
    }
  echo "    $(echo "$response" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("document_id","?")[:8]+"… status="+d.get("status","?"))' 2>/dev/null || echo "$response")"
done

echo ""
echo "Done. Monitor ingestion progress at http://localhost:3001/admin"
