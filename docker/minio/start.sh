#!/bin/sh
set -e

BUCKET="${S3_BUCKET:-rag-documents}"
MC_ALIAS="local"

# ---------------------------------------------------------------------------
# Start MinIO server in the background
# ---------------------------------------------------------------------------
echo "[minio] starting MinIO server..."
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}" \
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin123}" \
/usr/local/bin/minio server /data \
    --address ":9000" \
    --console-address ":9001" \
    --quiet &
MINIO_PID=$!

# Wait for MinIO to be ready (up to 30 seconds)
echo "[minio] waiting for MinIO to become ready..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:9000/minio/health/live" > /dev/null 2>&1; then
        echo "[minio] MinIO is ready."
        break
    fi
    sleep 1
done

# ---------------------------------------------------------------------------
# Initialize bucket
# ---------------------------------------------------------------------------
/usr/local/bin/mc alias set "${MC_ALIAS}" "http://localhost:9000" \
    "${MINIO_ROOT_USER:-minioadmin}" "${MINIO_ROOT_PASSWORD:-minioadmin123}" > /dev/null 2>&1 || true

if ! /usr/local/bin/mc ls "${MC_ALIAS}/${BUCKET}" > /dev/null 2>&1; then
    echo "[minio] creating bucket ${BUCKET}..."
    /usr/local/bin/mc mb "${MC_ALIAS}/${BUCKET}" || true
    /usr/local/bin/mc anonymous set none "${MC_ALIAS}/${BUCKET}" || true
    echo "[minio] bucket ${BUCKET} created."
else
    echo "[minio] bucket ${BUCKET} already exists."
fi

# ---------------------------------------------------------------------------
# Keep container alive (foreground the MinIO process)
# ---------------------------------------------------------------------------
echo "[minio] initialization complete — keeping MinIO in foreground."
wait "${MINIO_PID}"
