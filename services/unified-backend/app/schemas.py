"""
Merged Pydantic schemas for the unified backend.
Combines schemas from ingest-api and retrieval-api.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Ingest schemas ────────────────────────────────────────────────────────────

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
    source_type: str = "text"
    token_count: Optional[int] = None
    embedding_status: str = "pending"


class ChunksResponse(BaseModel):
    document_id: str
    total_chunks: int
    chunks: List[ChunkItem]


class ReprocessRequest(BaseModel):
    chunk_max_tokens: Optional[int] = None
    chunk_overlap_tokens: Optional[int] = None
    chunking_strategy: Optional[str] = None
    force_ocr: bool = False


# ── Retrieval schemas ─────────────────────────────────────────────────────────

class FilterSpec(BaseModel):
    document_ids: Optional[List[str]] = None
    language: Optional[str] = None
    source_type: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None


class RetrievalConfig(BaseModel):
    dense_candidates: int = Field(default=100, ge=10, le=500)
    sparse_candidates: int = Field(default=100, ge=10, le=500)
    rerank_candidates: int = Field(default=50, ge=5, le=200)
    mmr_lambda: float = Field(default=0.7, ge=0.0, le=1.0)
    enable_reranking: bool = True
    enable_ner: bool = False
    enable_stopword_removal_dense: bool = True
    enable_stopword_removal_sparse: bool = True
    k_rrf_dense: int = Field(default=60, ge=1, le=1000)
    k_rrf_sparse: int = Field(default=60, ge=1, le=1000)


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: Optional[int] = Field(default=None, ge=1, le=200)
    n: Optional[int] = Field(default=None, ge=1, le=50)
    mode: str = Field(default='chunks', pattern='^(chunks|documents)$')
    filters: Optional[FilterSpec] = None
    config: Optional[RetrievalConfig] = None


class ChunkResult(BaseModel):
    chunk_id: str
    parent_document_id: str
    chunk_text: str
    page_number: Optional[int] = None
    chunk_index: int = 0
    source_type: str = "text"
    cosine_score: float = 0.0
    bm25_score: float = 0.0
    rrf_score: float = 0.0
    rerank_score: Optional[float] = None


class DocumentResult(BaseModel):
    parent_document_id: str
    filename: str
    primary_chunk: ChunkResult
    supporting_chunks: List[ChunkResult] = []
    document_score: Optional[float] = None


class RetrievalResponse(BaseModel):
    query: str
    processed_query: str
    entities: List[str] = []
    mode: str
    k: Optional[int] = None
    n: Optional[int] = None
    chunks: Optional[List[ChunkResult]] = None
    documents: Optional[List[DocumentResult]] = None
    latency: dict = {}
    audit_id: str = ""
