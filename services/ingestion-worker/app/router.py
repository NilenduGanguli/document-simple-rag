import asyncio
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
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

    def merge_ocr(self, ocr_results: List):
        """Merge OCR results into full_text."""
        ocr_texts = []
        for result in ocr_results:
            if result and result.success and result.text:
                ocr_texts.append(f"[Page {result.page_number}] {result.text}")

        text_page_texts = [p['text'] for p in sorted(self.text_pages, key=lambda x: x['page_number'])]
        all_texts = text_page_texts + ocr_texts
        self.full_text = '\n\n'.join(filter(None, all_texts))

    def build_full_text(self):
        """Build full_text from text_pages only (no OCR)."""
        self.full_text = '\n\n'.join(
            p['text'] for p in sorted(self.text_pages, key=lambda x: x['page_number'])
        )


class IngestionRouter:
    TEXT_DENSITY_THRESHOLD = 0.05  # chars per pixel^2

    async def route(self, pdf_bytes: bytes, doc_id: str, force_ocr: bool = False) -> RoutingResult:
        """
        Inspect each PDF page and route to text extraction or OCR.
        Runs synchronous PyMuPDF in executor to not block event loop.

        Parameters
        ----------
        force_ocr: If True, all pages are rasterized and sent to OCR regardless
                   of their text density.
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._route_sync, pdf_bytes, doc_id, force_ocr)
        return result

    def _route_sync(self, pdf_bytes: bytes, doc_id: str, force_ocr: bool = False) -> RoutingResult:
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
        result = RoutingResult(document_id=doc_id, page_count=len(doc))

        for page_num, page in enumerate(doc):
            if force_ocr:
                # Force all pages through OCR by rasterizing at 300 DPI
                pix = page.get_pixmap(dpi=300)
                result.add_image(page_num, pix.tobytes('png'), is_full_page=True)
                continue

            text_blocks = page.get_text('blocks')
            text_content = ' '.join([b[4] for b in text_blocks if b[6] == 0])
            text_density = len(text_content.strip()) / max(page.rect.width * page.rect.height, 1)
            images = page.get_images(full=True)

            if text_density > self.TEXT_DENSITY_THRESHOLD:
                result.add_text_page(page_num, text_content)
            elif images:
                for img_meta in images:
                    try:
                        img_data = doc.extract_image(img_meta[0])
                        result.add_image(page_num, img_data['image'], img_meta)
                    except Exception as e:
                        logger.warning(f"Failed to extract image on page {page_num}: {e}")
            else:
                # Full page rasterization at 300 DPI
                pix = page.get_pixmap(dpi=300)
                result.add_image(page_num, pix.tobytes('png'), is_full_page=True)

        doc.close()
        if not result.images:
            result.build_full_text()

        logger.info(
            f"Routed doc {doc_id}: {result.page_count} pages, "
            f"{len(result.text_pages)} text, {len(result.images)} images"
            f"{' (force_ocr=True)' if force_ocr else ''}"
        )
        return result
