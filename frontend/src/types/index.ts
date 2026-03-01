// ── Auth types ──────────────────────────────────────────────────────────────

export interface AuthUser {
  username: string;
  role: string;
  name: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
  environment: string;
}

export interface AuthConfig {
  environment: string;
  auth_method: string;
}

// ── Ingestion types ──────────────────────────────────────────────────────────

export interface IngestResponse {
  document_id: string;
  status: string;
  message: string;
}

// ── Document types ──────────────────────────────────────────────────────────

export interface DocumentSummary {
  document_id: string;
  filename: string;
  status: string;
  page_count: number | null;
  file_size_bytes: number | null;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  chunk_count: number;
  error_message: string | null;
}

export interface DocumentListResponse {
  documents: DocumentSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface PipelineStageInfo {
  name: string;
  label: string;
  status: 'completed' | 'active' | 'pending' | 'failed';
  detail: string | null;
  model: string | null;
}

export interface DocumentPipelineStatus {
  document_id: string;
  filename: string;
  status: string;
  page_count: number | null;
  has_text: boolean;
  has_images: boolean;
  language_detected: string | null;
  file_size_bytes: number | null;
  s3_uri: string | null;
  error_message: string | null;
  retry_count: number;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  total_chunks: number;
  chunks_pending: number;
  chunks_processing: number;
  chunks_done: number;
  chunks_failed: number;
  total_embeddings: number;
  pipeline_stages: PipelineStageInfo[];
}

// ── Chunk types ─────────────────────────────────────────────────────────────

export interface ChunkItem {
  chunk_id: string;
  chunk_index: number;
  chunk_text: string;
  page_number: number | null;
  source_type: string;
  token_count: number | null;
  embedding_status: string;
}

export interface ChunksResponse {
  document_id: string;
  total_chunks: number;
  chunks: ChunkItem[];
}

// ── Presigned URL ───────────────────────────────────────────────────────────

export interface PresignedUrlResponse {
  document_id: string;
  url: string;
  expires_in: number;
  filename: string;
}

// ── Retrieval types ─────────────────────────────────────────────────────────

export interface RetrievalConfig {
  dense_candidates?: number;
  sparse_candidates?: number;
  rerank_candidates?: number;
  mmr_lambda?: number;
  enable_reranking?: boolean;
  enable_ner?: boolean;
  enable_stopword_removal_dense?: boolean;
  enable_stopword_removal_sparse?: boolean;
  k_rrf_dense?: number;
  k_rrf_sparse?: number;
}

export interface RetrievalRequest {
  query: string;
  mode: 'k_chunks' | 'n_documents';
  k?: number;
  n?: number;
  config?: RetrievalConfig;
}

export interface ChunkResult {
  chunk_id: string;
  parent_document_id: string;
  chunk_text: string;
  page_number: number | null;
  chunk_index: number;
  source_type: string;
  cosine_score: number;
  bm25_score: number;
  rrf_score: number;
  rerank_score: number | null;
}

export interface DocumentResult {
  parent_document_id: string;
  filename: string;
  primary_chunk: ChunkResult;
  supporting_chunks: ChunkResult[];
  document_score: number;
}

export interface RetrievalResponse {
  query: string;
  mode: string;
  audit_id: string;
  results_k_chunks: ChunkResult[] | null;
  results_n_documents: DocumentResult[] | null;
  total_results: number;
  latency_breakdown: Record<string, number>;
  entities_detected: string[];
}

// ── Stats types ─────────────────────────────────────────────────────────────

export interface DocumentStats {
  total: number;
  by_status: Record<string, number>;
}

export interface ChunkStats {
  total: number;
  total_embeddings: number;
  by_embedding_status: Record<string, number>;
}

export interface RetrievalStats {
  total_queries: number;
  avg_latency_ms: number | null;
  queries_last_24h: number;
}

export interface BM25Stats {
  index_size: number;
}

export interface SystemStats {
  documents: DocumentStats;
  chunks: ChunkStats;
  retrieval: RetrievalStats;
  bm25: BM25Stats;
}

// ── Admin / Reprocess types ───────────────────────────────────────────────────

export interface ReprocessParams {
  chunk_max_tokens: number;
  chunk_overlap_tokens: number;
  chunking_strategy: string;
  force_ocr: boolean;
}

export interface ReprocessResponse {
  document_id: string;
  status: string;
  chunks_cleared: number;
  message: string;
}
