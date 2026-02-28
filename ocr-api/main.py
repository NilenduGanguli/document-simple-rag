from fastapi import FastAPI, UploadFile, File, HTTPException
from openai import OpenAI
import base64
import io
import logging
import os
import time

import prometheus_client
from prometheus_client import Counter, Histogram
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────

api_key = os.getenv("OPENAI_API_KEY")
JAEGER_ENDPOINT = os.getenv("JAEGER_ENDPOINT", "http://jaeger:4317")
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "ocr-api")
METRICS_PORT = int(os.getenv("METRICS_PORT", "8083"))

# ── Tracing ───────────────────────────────────────────────────────────────────

try:
    resource = Resource.create({"service.name": OTEL_SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=JAEGER_ENDPOINT, insecure=True)
    processor = BatchSpanProcessor(
        exporter,
        max_queue_size=2048,
        max_export_batch_size=512,
        export_timeout_millis=10_000,
    )
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    logger.info("OpenTelemetry tracing configured for %s -> %s", OTEL_SERVICE_NAME, JAEGER_ENDPOINT)
except Exception as e:
    logger.warning("Failed to configure tracing: %s. Tracing disabled.", e)

tracer = trace.get_tracer(OTEL_SERVICE_NAME)

# ── Metrics ───────────────────────────────────────────────────────────────────

OCR_REQUESTS = Counter("ocr_api_requests_total", "Total OCR requests", ["status", "file_type"])
OCR_LATENCY = Histogram("ocr_api_latency_seconds", "OCR request latency", ["file_type"])
OCR_PAGES = Counter("ocr_api_pages_processed_total", "Total pages processed")

try:
    prometheus_client.start_http_server(METRICS_PORT)
    logger.info("Prometheus metrics server started on port %d", METRICS_PORT)
except Exception as e:
    logger.warning("Failed to start Prometheus metrics server: %s. Metrics unavailable.", e)

# ── Supported MIME types ──────────────────────────────────────────────────────

IMAGE_MIMES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp",
    "image/tiff", "image/svg+xml",
}
PDF_MIMES = {"application/pdf"}

# Map extensions to Vision API media types (OpenAI supports jpeg, png, gif, webp)
EXT_TO_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/png",   # will be converted
    ".tiff": "image/png",  # will be converted
    ".tif": "image/png",   # will be converted
    ".svg": "image/png",   # will be converted
    ".pdf": "application/pdf",
}


def _detect_file_type(filename: str, content_type: str | None) -> str:
    """Return a normalised MIME type from filename extension or upload header."""
    if filename:
        ext = os.path.splitext(filename.lower())[1]
        if ext in EXT_TO_MIME:
            return EXT_TO_MIME[ext]
    if content_type and content_type != "application/octet-stream":
        return content_type
    return "application/octet-stream"


def _convert_to_png(image_bytes: bytes) -> bytes:
    """Convert any PIL-supported image to PNG bytes for the Vision API."""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf_to_images(pdf_bytes: bytes) -> list[bytes]:
    """Convert each page of a PDF to PNG bytes using PyMuPDF."""
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="LLM OCR Service")

# Auto-instrument FastAPI routes with OpenTelemetry
try:
    FastAPIInstrumentor.instrument_app(app)
except Exception:
    pass


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/ocr")
async def perform_ocr(file: UploadFile = File(...)):
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")

    contents = await file.read()
    mime = _detect_file_type(file.filename or "", file.content_type)

    short_type = "pdf" if mime in PDF_MIMES else "image"
    start = time.time()

    try:
        with tracer.start_as_current_span("ocr.process", attributes={
            "file.name": file.filename or "unknown",
            "file.size": len(contents),
            "file.type": mime,
        }):
            if mime in PDF_MIMES:
                text = await _ocr_pdf(contents)
            elif mime in IMAGE_MIMES or mime == "application/octet-stream":
                text = await _ocr_single_image(contents, mime)
            else:
                # Attempt to treat unknown types as images
                text = await _ocr_single_image(contents, mime)

        confidence = 0.95 if text.strip() else 0.0
        OCR_REQUESTS.labels(status="success", file_type=short_type).inc()
        return {"text": text, "confidence": confidence}

    except Exception as e:
        OCR_REQUESTS.labels(status="error", file_type=short_type).inc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        OCR_LATENCY.labels(file_type=short_type).observe(time.time() - start)


async def _ocr_single_image(image_bytes: bytes, mime: str) -> str:
    """OCR a single image via OpenAI Vision API."""
    # Convert non-standard formats to PNG
    vision_mime = mime
    data = image_bytes
    if mime not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        data = _convert_to_png(image_bytes)
        vision_mime = "image/png"

    b64 = base64.b64encode(data).decode("utf-8")
    client = OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Extract all text from this image. Return only the text content without any markdown formatting."},
                {"type": "image_url", "image_url": {"url": f"data:{vision_mime};base64,{b64}"}},
            ],
        }],
        max_tokens=3000,
    )
    OCR_PAGES.inc()
    return response.choices[0].message.content or ""


async def _ocr_pdf(pdf_bytes: bytes) -> str:
    """Convert PDF pages to images and OCR each one."""
    with tracer.start_as_current_span("ocr.pdf_to_images"):
        images = _pdf_to_images(pdf_bytes)

    all_text = []
    for i, img_bytes in enumerate(images):
        with tracer.start_as_current_span(f"ocr.page_{i}"):
            page_text = await _ocr_single_image(img_bytes, "image/png")
            all_text.append(page_text)

    return "\n\n".join(all_text)
