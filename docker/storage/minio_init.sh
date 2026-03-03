#!/bin/sh
# Waits for MinIO to be ready, then creates required buckets.
set -e

MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin123}"

echo "[minio-init] waiting for MinIO at ${MINIO_ENDPOINT}..."
until mc alias set myminio "${MINIO_ENDPOINT}" "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" 2>/dev/null; do
    echo "[minio-init] MinIO not ready, retrying in 5 seconds..."
    sleep 5
done

echo "[minio-init] creating bucket..."
mc mb --ignore-existing myminio/rag-documents

echo "[minio-init] bucket initialization complete"
