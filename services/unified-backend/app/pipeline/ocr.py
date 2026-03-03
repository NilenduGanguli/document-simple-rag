"""
OCR via the dedicated ocr-api container.

Forwards images over HTTP to the ocr-api service rather than calling OpenAI
directly. This keeps the OpenAI key isolated to the ocr-api container and
allows the OCR service to be scaled or swapped independently.

Environment variables
---------------------
OCR_API_URL          URL of the OCR API endpoint    (default: http://ocr-api:8002/ocr)
OCR_TIMEOUT_SECONDS  Per-request timeout in seconds  (default: 120)
"""
from __future__ import annotations

import logging
import os
from typing import Tuple

import httpx

logger = logging.getLogger(__name__)

_OCR_API_URL = os.getenv("OCR_API_URL", "http://ocr-api:8002/ocr")
_OCR_TIMEOUT = float(os.getenv("OCR_TIMEOUT_SECONDS", "120"))


async def perform_ocr_image(image_bytes: bytes, mime_type: str = "image/png") -> Tuple[str, float]:
    """
    OCR a single image by forwarding it to the ocr-api container.

    Returns:
        (extracted_text, confidence_score)
        confidence_score is 1.0 on success, 0.0 on failure.

    Returns ("", 0.0) when the ocr-api is unreachable or returns an error,
    so the ingestion pipeline continues without crashing.
    """
    try:
        async with httpx.AsyncClient(timeout=_OCR_TIMEOUT) as client:
            response = await client.post(
                _OCR_API_URL,
                files={"file": ("image.png", image_bytes, mime_type)},
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("text", ""), float(payload.get("confidence", 1.0))

    except httpx.HTTPStatusError as exc:
        logger.error(
            f"ocr-api returned HTTP {exc.response.status_code}: "
            f"{exc.response.text[:200]}"
        )
        return "", 0.0

    except httpx.ConnectError:
        logger.error(f"ocr-api unreachable at {_OCR_API_URL}")
        return "", 0.0

    except Exception as exc:
        logger.error(f"OCR request failed: {exc}")
        return "", 0.0
