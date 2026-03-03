import asyncio
from dataclasses import dataclass, field
from typing import List
import fitz  # PyMuPDF
import logging

logger = logging.getLogger(__name__)


@dataclass
class ImageData:
    page_number: int
    image_bytes: bytes
    is_full_page: bool = False
    img_meta: tuple = None


@dataclass
class RoutingResult:
    document_id: str
    text_pages: List[dict] = field(default_factory=list)
    images: List[ImageData] = field(default_factory=list)
    full_text: str = ""
    page_count: int = 0
    has_text: bool = False
    has_images: bool = False

    def add_text_page(self, page_num: int, text: str):
        self.text_pages.append({'page_number': page_num, 'text': text})
        self.has_text = True

    def add_image(self, page_num: int, image_bytes: bytes, img_meta=None, is_full_page: bool = False):
        self.images.append(ImageData(
            page_number=page_num,
            image_bytes=image_bytes,
            is_full_page=is_full_page,
            img_meta=img_meta
        ))
        self.has_images = True

    def merge_ocr(self, ocr_results: List) -> None:
        """
        Merge extracted text pages and OCR results in document page order.

        Text content and OCR content from the same page are placed adjacent
        to preserve reading order for mixed text+image pages.
        """
        # Accumulate content per page: {page_number: [text_piece, ...]}
        page_content: dict[int, list[str]] = {}

        for tp in self.text_pages:
            pn = tp['page_number']
            page_content.setdefault(pn, []).append(tp['text'])

        for ocr in ocr_results:
            if ocr and ocr.success and ocr.text:
                page_content.setdefault(ocr.page_number, []).append(ocr.text)

        self.full_text = '\n\n'.join(
            '\n'.join(pieces)
            for _, pieces in sorted(page_content.items())
        )

    def build_full_text(self) -> None:
        """Build full_text from text_pages only (pure text document, no OCR)."""
        self.full_text = '\n\n'.join(
            p['text'] for p in sorted(self.text_pages, key=lambda x: x['page_number'])
        )


class IngestionRouter:
    TEXT_DENSITY_THRESHOLD = 0.0008  # chars per PDF-point^2 — pages with extractable text exceed this
    # Rationale: A4 page area ≈ 500,990 pt².  A page with ~400 meaningful chars
    # yields ~0.0008.  Scanned/image-only pages yield 0.0 (no selectable text).
    # Using 0.0008 instead of 0.001 provides margin for short-but-valid text pages.

    async def route(self, pdf_bytes: bytes, doc_id: str, force_ocr: bool = False) -> RoutingResult:
        """
        Inspect each PDF page and route content to text extraction or OCR.

        Page routing rules
        ------------------
        force_ocr=True   → every page is rasterised at 300 DPI and sent to OCR.
        Otherwise, per page:
          • Selectable text (density > threshold) → added to text_pages.
          • Embedded images (any page, even mixed text+image) → every image is
            extracted and queued for OCR individually.
          • Pages with neither selectable text nor embedded images → full-page
            rasterisation at 300 DPI sent to OCR.

        Mixed pages (text + images) produce BOTH a text_page entry AND image
        entries so that both the selectable text and any image content are
        captured.  merge_ocr() interleaves the results in page order.
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._route_sync, pdf_bytes, doc_id, force_ocr)
        return result

    def _route_sync(self, pdf_bytes: bytes, doc_id: str, force_ocr: bool = False) -> RoutingResult:
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
        result = RoutingResult(document_id=doc_id, page_count=len(doc))

        for page_num, page in enumerate(doc):
            if force_ocr:
                pix = page.get_pixmap(dpi=300)
                result.add_image(page_num, pix.tobytes('png'), is_full_page=True)
                continue

            # ── Extract selectable text ───────────────────────────────────
            text_blocks = page.get_text('blocks')
            text_content = ' '.join([b[4] for b in text_blocks if b[6] == 0])
            text_density = len(text_content.strip()) / max(page.rect.width * page.rect.height, 1)
            has_text = text_density > self.TEXT_DENSITY_THRESHOLD

            # ── Detect embedded images ────────────────────────────────────
            embedded_images = page.get_images(full=True)

            if has_text:
                result.add_text_page(page_num, text_content)

            if embedded_images:
                # Extract every embedded image from this page and queue for OCR.
                # This applies even when the page also has selectable text so
                # that charts, diagrams and photos are not silently ignored.
                for img_meta in embedded_images:
                    try:
                        img_data = doc.extract_image(img_meta[0])
                        result.add_image(page_num, img_data['image'], img_meta)
                    except Exception as exc:
                        logger.warning(f"Failed to extract image xref={img_meta[0]} on page {page_num}: {exc}")
            elif not has_text:
                # No selectable text, no embedded images → full-page rasterise
                pix = page.get_pixmap(dpi=300)
                result.add_image(page_num, pix.tobytes('png'), is_full_page=True)

        doc.close()

        logger.info(
            f"Routed doc {doc_id}: {result.page_count} pages, "
            f"{len(result.text_pages)} text pages, {len(result.images)} image(s) for OCR"
            f"{' (force_ocr)' if force_ocr else ''}"
        )
        return result
