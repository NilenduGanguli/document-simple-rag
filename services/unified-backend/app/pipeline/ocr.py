"""
Inline OCR via OpenAI Vision API.

Adapted from ocr-api/main.py. Called directly in the ingestion worker.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from typing import Tuple

logger = logging.getLogger(__name__)

_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Map extensions / content types to Vision API media types
_VISION_MIME = {
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
    "image/bmp": "image/png",
    "image/tiff": "image/png",
    "image/svg+xml": "image/png",
}


def _convert_to_png(image_bytes: bytes) -> bytes:
    """Convert any PIL-supported image to PNG bytes."""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def perform_ocr_image(image_bytes: bytes, mime_type: str = "image/png") -> Tuple[str, float]:
    """
    OCR a single image using OpenAI Vision API.

    Returns:
        (extracted_text, confidence_score)
        confidence_score is always 1.0 (OpenAI does not return confidence).

    Returns ("", 0.0) if OPENAI_API_KEY is not set or on API error.
    """
    if not _OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — OCR disabled; returning empty text")
        return "", 0.0

    # Normalise MIME to a Vision-compatible type
    vision_mime = _VISION_MIME.get(mime_type, "image/png")

    # Convert non-JPEG/PNG/GIF/WEBP formats to PNG
    if vision_mime == "image/png" and mime_type not in ("image/png",):
        try:
            image_bytes = _convert_to_png(image_bytes)
        except Exception as exc:
            logger.warning(f"Image conversion to PNG failed: {exc} — using raw bytes")

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    try:
        from openai import OpenAI
        client = OpenAI(api_key=_OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Extract all text from this image exactly as it appears. "
                            "Preserve formatting, line breaks, and structure. "
                            "Return only the extracted text without any additional commentary."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{vision_mime};base64,{b64}"},
                    },
                ],
            }],
            max_tokens=3000,
        )
        text = response.choices[0].message.content or ""
        return text, 1.0
    except Exception as exc:
        logger.error(f"OpenAI OCR call failed: {exc}")
        return "", 0.0
