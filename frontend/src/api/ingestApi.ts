import { apiClient } from './client';
import type { IngestResponse, ReprocessParams, ReprocessResponse } from '../types';

export const ingestApi = {
  uploadDocument: (file: File) =>
    apiClient.uploadFile<IngestResponse>('/api/ingest/documents/ingest', file),

  deleteDocument: (id: string) =>
    apiClient.request<{ document_id: string; status: string; message: string }>(
      'DELETE',
      `/api/ingest/documents/${id}`,
    ),

  reprocessDocument: (id: string, params: ReprocessParams) =>
    apiClient.request<ReprocessResponse>('POST', `/api/ingest/documents/${id}/reprocess`, params),
};
