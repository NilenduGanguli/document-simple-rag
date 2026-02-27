import { apiClient } from './client';
import type { IngestResponse } from '../types';

export const ingestApi = {
  uploadDocument: (file: File) =>
    apiClient.uploadFile<IngestResponse>('/api/ingest/documents/ingest', file),

  deleteDocument: (id: string) =>
    apiClient.request<{ document_id: string; status: string }>('DELETE', `/api/ingest/documents/${id}`),
};
