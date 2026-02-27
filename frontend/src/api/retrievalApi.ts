import { apiClient } from './client';
import type {
  DocumentListResponse,
  DocumentPipelineStatus,
  ChunksResponse,
  PresignedUrlResponse,
  RetrievalRequest,
  RetrievalResponse,
  SystemStats,
} from '../types';

function qs(params: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  }
  return p.toString();
}

export const retrievalApi = {
  listDocuments: (params: { limit?: number; offset?: number; status?: string } = {}) =>
    apiClient.request<DocumentListResponse>('GET', `/api/retrieval/documents?${qs(params)}`),

  getDocumentPipeline: (id: string) =>
    apiClient.request<DocumentPipelineStatus>('GET', `/api/retrieval/documents/${id}`),

  getChunks: (id: string, params: { limit?: number; offset?: number } = {}) =>
    apiClient.request<ChunksResponse>('GET', `/api/retrieval/documents/${id}/chunks?${qs(params)}`),

  getDownloadUrl: (id: string) =>
    apiClient.request<PresignedUrlResponse>('GET', `/api/retrieval/documents/${id}/download-url`),

  retrieve: (body: RetrievalRequest) =>
    apiClient.request<RetrievalResponse>('POST', '/api/retrieval/retrieve', body),

  getAudit: (auditId: string) =>
    apiClient.request<Record<string, unknown>>('GET', `/api/retrieval/retrieve/audit/${auditId}`),

  getStats: () =>
    apiClient.request<SystemStats>('GET', '/api/retrieval/stats'),
};
