"""
Pydantic v2 schemas for the RAG Retrieval API.

Covers:
  - FilterSpec       — optional document / language / date filters
  - RetrievalConfig  — per-request pipeline tuning knobs
  - RetrievalRequest — top-level request body
  - ChunkResult      — single chunk returned in a retrieval result set
  - DocumentResult   — parent document aggregation for n_documents mode
  - RetrievalResponse — full API response
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class FilterSpec(BaseModel):
    """Narrow the retrieval search to a subset of documents."""

    document_ids: Optional[List[str]] = None
    language: Optional[str] = None
    source_type: Optional[Literal['text', 'ocr', 'mixed']] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None


class RetrievalConfig(BaseModel):
    """Per-request pipeline configuration parameters."""

    dense_candidates: int = Field(
        default=100,
        ge=10,
        le=500,
        description="Number of HNSW nearest-neighbour candidates to retrieve.",
    )
    sparse_candidates: int = Field(
        default=100,
        ge=10,
        le=500,
        description="Number of BM25 candidates to retrieve.",
    )
    rerank_candidates: int = Field(
        default=50,
        ge=5,
        le=200,
        description="Number of top RRF results passed to the cross-encoder.",
    )
    mmr_lambda: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="MMR relevance/diversity trade-off (1.0 = pure relevance).",
    )
    enable_reranking: bool = Field(
        default=True,
        description="Enable cross-encoder reranking stage.",
    )
    enable_ner: bool = Field(
        default=False,
        description="Enable NER-based query preprocessing (disabled by default).",
    )
    k_rrf_dense: int = Field(
        default=60,
        ge=1,
        le=1000,
        description=(
            "RRF smoothing parameter for the dense (semantic) ranking list. "
            "Lower values increase the influence of top-ranked dense results."
        ),
    )
    k_rrf_sparse: int = Field(
        default=60,
        ge=1,
        le=1000,
        description=(
            "RRF smoothing parameter for the sparse (BM25) ranking list. "
            "Lower values increase the influence of top-ranked BM25 results. "
            "Set lower than k_rrf_dense to up-weight keyword matches."
        ),
    )


class RetrievalRequest(BaseModel):
    """Top-level hybrid retrieval request."""

    query: str = Field(
        min_length=1,
        max_length=500,
        description="Natural-language question or search query.",
    )
    mode: Literal['k_chunks', 'n_documents'] = Field(
        default='k_chunks',
        description=(
            "'k_chunks' returns the top-k individual text chunks; "
            "'n_documents' groups results by parent document."
        ),
    )
    k: Optional[int] = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of chunks to return (used when mode='k_chunks').",
    )
    n: Optional[int] = Field(
        default=5,
        ge=1,
        le=50,
        description="Number of documents to return (used when mode='n_documents').",
    )
    filters: Optional[FilterSpec] = None
    config: Optional[RetrievalConfig] = None


class ChunkResult(BaseModel):
    """A single retrieved text chunk with its provenance and scores."""

    chunk_id: str
    parent_document_id: str
    chunk_text: str
    page_number: Optional[int] = None
    chunk_index: int
    source_type: str
    cosine_score: float
    bm25_score: float = 0.0
    rrf_score: float = 0.0
    rerank_score: Optional[float] = None


class DocumentResult(BaseModel):
    """
    Aggregated result for 'n_documents' mode.
    Contains the best-scoring chunk as the primary and supporting chunks.
    """

    parent_document_id: str
    filename: str
    primary_chunk: ChunkResult
    supporting_chunks: List[ChunkResult]
    document_score: float


class RetrievalResponse(BaseModel):
    """Full retrieval response with optional results, audit info, and timing."""

    query: str
    mode: str
    audit_id: str
    results_k_chunks: Optional[List[ChunkResult]] = None
    results_n_documents: Optional[List[DocumentResult]] = None
    total_results: int
    latency_breakdown: Dict[str, float]
    entities_detected: List[str] = []
