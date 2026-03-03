"""
OCR API — dedicated FastAPI service for image-to-text extraction.

Accepts a single image upload via POST /ocr and returns extracted text
using the OpenAI Vision API (gpt-4o-mini).

OPENAI_API_KEY is mandatory.  The service refuses to start without it.

Environment variables
---------------------
OPENAI_API_KEY   Required — OpenAI API key for Vision calls.
OCR_MODEL        Vision model to use (default: gpt-4o-mini).
OCR_MAX_TOKENS   Max tokens in the Vision response (default: 3000).
"""
from __future__ import annotations

import base64
import io
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OCR_MODEL      = os.getenv("OCR_MODEL", "gpt-4o-mini")
OCR_MAX_TOKENS = int(os.getenv("OCR_MAX_TOKENS", "3000"))

# MIME types that OpenAI Vision accepts natively
_NATIVE_VISION_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


# ---------------------------------------------------------------------------
# Startup validation — crash the container if the key is missing
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not OPENAI_API_KEY:
        logger.critical(
            "OPENAI_API_KEY is not set. "
            "The ocr-api container requires a valid OpenAI API key to perform Vision OCR. "
            "Set OPENAI_API_KEY in the environment and restart the container."
        )
        sys.exit(1)
    logger.info(f"ocr-api ready — model={OCR_MODEL} max_tokens={OCR_MAX_TOKENS}")
    yield


app = FastAPI(title="OCR API", version="1.0.0", lifespan=lifespan)


class OCRResponse(BaseModel):
    text: str
    confidence: float


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "model": OCR_MODEL}


# ---------------------------------------------------------------------------
# OCR endpoint
# ---------------------------------------------------------------------------

@app.post("/ocr", response_model=OCRResponse)
async def ocr_image(file: UploadFile = File(...)):
    """
    Extract text from an uploaded image using OpenAI Vision.

    Accepts any image format; non-native formats are converted to PNG before
    being sent to the Vision API.
    """
    image_bytes = await file.read()
    mime = file.content_type or "image/png"

    # Convert unsupported formats to PNG
    if mime not in _NATIVE_VISION_MIMES:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            image_bytes = buf.getvalue()
            mime = "image/png"
        except Exception as exc:
            logger.warning(f"Image conversion to PNG failed: {exc} — sending raw bytes")

    b64 = base64.b64encode(image_bytes).decode()

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model=OCR_MODEL,
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
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }],
            max_tokens=OCR_MAX_TOKENS,
        )
        text = response.choices[0].message.content or ""
        logger.info(f"OCR completed: {len(text)} chars extracted from {file.filename!r}")
        return OCRResponse(text=text, confidence=1.0)

    except Exception as exc:
        logger.error(f"OpenAI Vision call failed: {exc}")
        raise HTTPException(status_code=502, detail=f"OCR failed: {exc}")
