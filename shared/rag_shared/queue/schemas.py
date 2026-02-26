from dataclasses import dataclass, field
from typing import Optional, List
import uuid
import time


@dataclass
class IngestionTask:
    parent_document_id: str
    s3_bucket: str
    s3_key: str
    filename: str
    file_size_bytes: int
    mime_type: str = 'application/pdf'
    priority: int = 5
    retry_count: int = 0
    created_at: float = field(default_factory=time.time)
    source_metadata: dict = field(default_factory=dict)


@dataclass
class OCRTask:
    parent_document_id: str
    page_number: int
    image_bytes: bytes
    is_full_page: bool = False
    reply_correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class OCRResult:
    correlation_id: str
    page_number: int
    text: str
    confidence: float = 0.0
    success: bool = True
    error: Optional[str] = None


@dataclass
class EmbeddingTask:
    chunk_ids: List[str]
    parent_document_id: str
    batch_index: int = 0
