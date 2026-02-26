"""
Async S3 / MinIO client built on aioboto3.

All public methods use async context managers internally so no persistent
session state leaks between calls.  Pass endpoint_url to redirect to a
MinIO (or any S3-compatible) endpoint.
"""

import logging
from typing import IO, Optional

import aioboto3

logger = logging.getLogger(__name__)


class S3Client:
    """
    High-level async wrapper around aioboto3 S3 operations.

    Parameters
    ----------
    access_key:    AWS / MinIO access key ID
    secret_key:    AWS / MinIO secret access key
    region:        AWS region name (default "us-east-1")
    endpoint_url:  Override endpoint for MinIO or localstack. None uses AWS.
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
    ) -> None:
        self._session = aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        self._endpoint_url = endpoint_url
        self._region = region

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helper
    # ──────────────────────────────────────────────────────────────────────────

    def _client(self):
        """Return an async context manager that yields a boto3 S3 client."""
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def upload_file_streaming(
        self,
        file_obj: IO[bytes],
        bucket: str,
        key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """
        Upload a file-like object to S3 / MinIO using multipart upload.

        Parameters
        ----------
        file_obj:     Readable binary file-like object (supports large files).
        bucket:       Target bucket name.
        key:          Object key (path inside the bucket).
        content_type: MIME type stored as object metadata.

        Returns
        -------
        s3_uri: ``s3://<bucket>/<key>``
        """
        async with self._client() as s3:
            await s3.upload_fileobj(
                file_obj,
                bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )

        s3_uri = f"s3://{bucket}/{key}"
        logger.info(f"Uploaded to {s3_uri} (content_type={content_type})")
        return s3_uri

    async def download_file(self, bucket: str, key: str) -> bytes:
        """
        Download an object and return its full content as bytes.

        Suitable for files up to a few hundred MB.  For very large files
        consider streaming via get_object and iterating the body chunks.
        """
        async with self._client() as s3:
            response = await s3.get_object(Bucket=bucket, Key=key)
            async with response["Body"] as stream:
                data = await stream.read()

        logger.debug(f"Downloaded s3://{bucket}/{key} ({len(data):,} bytes)")
        return data

    async def delete_file(self, bucket: str, key: str) -> None:
        """
        Permanently delete an object from S3 / MinIO.

        Silently succeeds if the object does not exist (S3 semantics).
        """
        async with self._client() as s3:
            await s3.delete_object(Bucket=bucket, Key=key)

        logger.info(f"Deleted s3://{bucket}/{key}")

    async def get_presigned_url(
        self,
        bucket: str,
        key: str,
        expires: int = 3600,
    ) -> str:
        """
        Generate a pre-signed GET URL for temporary public access.

        Parameters
        ----------
        bucket:  Bucket containing the object.
        key:     Object key.
        expires: URL lifetime in seconds (default 3600 / 1 hour).

        Returns
        -------
        Presigned HTTPS URL string.
        """
        async with self._client() as s3:
            url = await s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires,
            )

        logger.debug(f"Presigned URL generated for s3://{bucket}/{key} (expires={expires}s)")
        return url
