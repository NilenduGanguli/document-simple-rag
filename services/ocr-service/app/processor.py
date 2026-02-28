import hashlib
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_OCR_API_URL: str = os.getenv("OCR_API_URL", "http://ocr-api:8002/ocr")


class OCRProcessor:
    """OCR processor that delegates to the external ocr-api REST service."""

    def __init__(self):
        self.ocr_api_url: str = _OCR_API_URL

    async def process(self, image_bytes: bytes) -> tuple[str, float]:
        """Send image bytes to ocr-api and return ``(text, confidence)``."""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    self.ocr_api_url,
                    files={"file": ("image.png", image_bytes, "image/png")},
                )
                response.raise_for_status()
                payload = response.json()
                text: str = payload.get("text", "")
                confidence: float = float(payload.get("confidence", 0.0))
                return text, confidence
        except Exception as e:
            logger.error("OCR API request failed: %s", e)
            raise

    @staticmethod
    def compute_image_hash(image_bytes: bytes) -> str:
        return hashlib.sha256(image_bytes).hexdigest()
