#!/bin/sh
set -e

echo "Waiting for MinIO..."
until mc alias set myminio http://minio:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD}; do
  echo "MinIO not ready, retrying in 5 seconds..."
  sleep 5
done

echo "Creating bucket..."
mc mb --ignore-existing myminio/rag-documents

echo "MinIO initialization complete."
