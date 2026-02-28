from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class IngestResponse(BaseModel):
    document_id: str
    status: str
    message: str


class DocumentStatus(BaseModel):
    document_id: str
    filename: str
    status: str
    page_count: Optional[int] = None
    has_text: bool = False
    has_images: bool = False
    language_detected: Optional[str] = None
    s3_uri: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ChunkItem(BaseModel):
    chunk_id: str
    chunk_index: int
    chunk_text: str
    page_number: Optional[int] = None
    source_type: str = 'text'
    token_count: Optional[int] = None
    embedding_status: str = 'pending'


class ChunksResponse(BaseModel):
    document_id: str
    total_chunks: int
    chunks: list[ChunkItem]


class ReprocessRequest(BaseModel):
    chunk_max_tokens: int = Field(
        default=400,
        ge=50,
        le=1000,
        description="Maximum tokens per chunk for this reprocess run.",
    )
    chunk_overlap_tokens: int = Field(
        default=50,
        ge=0,
        le=200,
        description="Overlap tokens between consecutive chunks.",
    )
    chunking_strategy: str = Field(
        default="recursive",
        description="Chunking strategy to use (currently only 'recursive').",
    )
    force_ocr: bool = Field(
        default=False,
        description="Force OCR even for pages that have extractable text.",
    )
    ocr_languages: List[str] = Field(
        default_factory=lambda: ["eng"],
        description="Tesseract language codes for OCR (e.g. ['eng', 'fra']).",
    )
