#!/usr/bin/env python3
"""
s3_ensure.py — Idempotent S3 / MinIO bucket bootstrap for the RAG backend.

Runs at container startup (before uvicorn) to guarantee that the required
S3 bucket exists.  This is a no-op when:
  • the bucket already exists and is owned by this account (MinIO or AWS)
  • the bucket already exists on AWS and was created by the same account

Safe to call on every container restart.

Environment variables
---------------------
S3_ENDPOINT_URL   Override endpoint for MinIO or any S3-compatible service.
                  Leave unset (or empty) to use real AWS S3.
                  Example:  http://minio:9000

S3_ACCESS_KEY     Access key ID.   Alias: AWS_ACCESS_KEY_ID
S3_SECRET_KEY     Secret key.      Alias: AWS_SECRET_ACCESS_KEY
S3_REGION         AWS region (default: us-east-1).
                  Ignored for MinIO / S3-compatible services.

S3_BUCKET         Name of the application documents bucket (default: rag-documents).
                  This is the only bucket this script ensures.

S3_EXTERNAL_URL   Browser-facing base URL for presigned download links.
                  Only used by the application layer — not by this script.
                  Set to the public hostname when MinIO is behind a load balancer
                  or gateway (e.g. https://storage.example.com).
                  For local dev with MinIO: http://localhost:19000
                  For AWS S3: leave unset — presigned URLs already use the
                  public S3 endpoint.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import aioboto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [s3-ensure] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (read from environment)
# ---------------------------------------------------------------------------

S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "").strip() or None
S3_ACCESS_KEY   = os.environ.get("S3_ACCESS_KEY",   os.environ.get("AWS_ACCESS_KEY_ID",     ""))
S3_SECRET_KEY   = os.environ.get("S3_SECRET_KEY",   os.environ.get("AWS_SECRET_ACCESS_KEY", ""))
S3_REGION       = os.environ.get("S3_REGION",       "us-east-1")
S3_BUCKET       = os.environ.get("S3_BUCKET",       "rag-documents")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_bucket_if_missing(s3_client, bucket: str, region: str) -> None:
    """
    Create *bucket* if it does not already exist.

    Handles both MinIO (no real region concept) and AWS S3 (requires
    LocationConstraint for all regions except us-east-1).
    """
    try:
        await s3_client.head_bucket(Bucket=bucket)
        logger.info(f"Bucket already exists: s3://{bucket}")
        return
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code not in ("404", "NoSuchBucket"):
            # 403 = exists but no access; anything else is unexpected
            raise

    # Bucket does not exist — create it
    create_kwargs: dict = {"Bucket": bucket}
    # AWS S3 requires LocationConstraint for every region except us-east-1
    if not S3_ENDPOINT_URL and region != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}

    try:
        await s3_client.create_bucket(**create_kwargs)
        logger.info(f"Created bucket: s3://{bucket}")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            # Race condition or MinIO equivalent — bucket exists, that's fine
            logger.info(f"Bucket already exists (concurrent creation): s3://{bucket}")
        else:
            raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    if not S3_ACCESS_KEY or not S3_SECRET_KEY:
        logger.error(
            "S3_ACCESS_KEY and S3_SECRET_KEY (or AWS_ACCESS_KEY_ID / "
            "AWS_SECRET_ACCESS_KEY) must be set."
        )
        sys.exit(1)

    target = S3_ENDPOINT_URL if S3_ENDPOINT_URL else "AWS S3"
    logger.info(f"S3 bootstrap — endpoint={target}  bucket={S3_BUCKET}  region={S3_REGION}")

    session = aioboto3.Session(
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
    )

    async with session.client("s3", endpoint_url=S3_ENDPOINT_URL) as s3:
        await _create_bucket_if_missing(s3, S3_BUCKET, S3_REGION)

    logger.info("S3 bootstrap complete.")


if __name__ == "__main__":
    asyncio.run(main())
