import hashlib
import asyncio
import io
import logging
import os
from typing import Optional

import httpx
from PIL import Image
import pytesseract

logger = logging.getLogger(__name__)

# When USE_OCR_API=true the processor delegates to the external ocr-api REST
# service instead of running Tesseract locally.  This trades local CPU/memory
# usage for a network call, which is useful when Tesseract resources are tight.
_USE_OCR_API: bool = os.getenv("USE_OCR_API", "false").lower() == "true"
_OCR_API_URL: str = os.getenv("OCR_API_URL", "http://ocr-api:8002/ocr")


class OCRProcessor:
    """
    OCR processor with two back-ends:

    * Tesseract (default) — runs locally in a thread-pool executor.
    * OCR API             — delegates to an external REST service when
                           ``USE_OCR_API=true`` is set in the environment.

    Both paths return the same ``(text: str, confidence: float)`` tuple so
    the rest of the codebase is unaffected by the choice of back-end.
    """

    def __init__(self, languages: str = "eng"):
        self.languages = languages
        self.tesseract_config = "--oem 3 --psm 3"
        self.use_ocr_api: bool = _USE_OCR_API
        self.ocr_api_url: str = _OCR_API_URL

    async def process(self, image_bytes: bytes) -> tuple[str, float]:
        """
        Process image bytes and return ``(text, confidence)``.

        Routes to the external OCR API when ``use_ocr_api=True``, otherwise
        runs Tesseract in a thread-pool executor to avoid blocking the loop.
        """
        if self.use_ocr_api:
            return await self._process_via_api(image_bytes)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._process_sync, image_bytes)

    # ------------------------------------------------------------------
    # OCR API back-end
    # ------------------------------------------------------------------

    async def _process_via_api(self, image_bytes: bytes) -> tuple[str, float]:
        """Send image_bytes to the external OCR REST API."""
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

    # ------------------------------------------------------------------
    # Tesseract back-end (original implementation, unchanged)
    # ------------------------------------------------------------------

    def _process_sync(self, image_bytes: bytes) -> tuple[str, float]:
        try:
            image = Image.open(io.BytesIO(image_bytes))

            # Convert to RGB if needed
            if image.mode not in ('RGB', 'L'):
                image = image.convert('RGB')

            # Get text with confidence data
            data = pytesseract.image_to_data(
                image,
                lang=self.languages,
                config=self.tesseract_config,
                output_type=pytesseract.Output.DICT
            )

            # Extract confident words
            words = []
            confidences = []
            for i, conf in enumerate(data['conf']):
                if int(conf) > 30:  # Filter low-confidence words
                    word = data['text'][i].strip()
                    if word:
                        words.append(word)
                        confidences.append(int(conf))

            text = ' '.join(words)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

            return text, avg_confidence / 100.0

        except Exception as e:
            logger.error(f"Tesseract OCR failed: {e}")
            raise

    @staticmethod
    def compute_image_hash(image_bytes: bytes) -> str:
        return hashlib.sha256(image_bytes).hexdigest()
