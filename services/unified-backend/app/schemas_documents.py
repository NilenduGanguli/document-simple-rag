"""Pydantic schemas for document management and system stats endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class DocumentSummary(BaseModel):
    document_id: str
    filename: str
    status: str
    page_count: Optional[int] = None
    file_size_bytes: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    chunk_count: int = 0
    error_message: Optional[str] = None


class DocumentListResponse(BaseModel):
    documents: List[DocumentSummary]
    total: int
    limit: int
    offset: int


class PipelineStageInfo(BaseModel):
    name: str
    label: str
    status: str  # "completed", "active", "pending", "failed"
    detail: Optional[str] = None
    model: Optional[str] = None


class DocumentPipelineStatus(BaseModel):
    document_id: str
    filename: str
    status: str
    page_count: Optional[int] = None
    has_text: bool = False
    has_images: bool = False
    language_detected: Optional[str] = None
    file_size_bytes: Optional[int] = None
    s3_uri: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_chunks: int = 0
    chunks_pending: int = 0
    chunks_processing: int = 0
    chunks_done: int = 0
    chunks_failed: int = 0
    total_embeddings: int = 0
    pipeline_stages: List[PipelineStageInfo] = []


class ChunkItem(BaseModel):
    chunk_id: str
    chunk_index: int
    chunk_text: str
    page_number: Optional[int] = None
    source_type: str = "text"
    token_count: Optional[int] = None
    embedding_status: str = "pending"


class ChunksResponse(BaseModel):
    document_id: str
    total_chunks: int
    chunks: List[ChunkItem]


class PresignedUrlResponse(BaseModel):
    document_id: str
    url: str
    expires_in: int
    filename: str


class DocumentStats(BaseModel):
    total: int = 0
    by_status: Dict[str, int] = {}


class ChunkStats(BaseModel):
    total: int = 0
    total_embeddings: int = 0
    by_embedding_status: Dict[str, int] = {}


class RetrievalStats(BaseModel):
    total_queries: int = 0
    avg_latency_ms: Optional[float] = None
    queries_last_24h: int = 0


class BM25Stats(BaseModel):
    index_size: int = 0


class SystemStats(BaseModel):
    documents: DocumentStats = DocumentStats()
    chunks: ChunkStats = ChunkStats()
    retrieval: RetrievalStats = RetrievalStats()
    bm25: BM25Stats = BM25Stats()
