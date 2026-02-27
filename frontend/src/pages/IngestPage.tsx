import { useState, useCallback } from 'react';
import UploadForm from '../components/documents/UploadForm';
import DocumentList from '../components/documents/DocumentList';
import DocumentDetail from '../components/documents/DocumentDetail';
import PipelineMonitor from '../components/pipeline/PipelineMonitor';
import { useDocuments } from '../hooks/useDocuments';
import { ingestApi } from '../api/ingestApi';

export default function IngestPage() {
  const { documents, isLoading, refetch } = useDocuments();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);

  const handleUploaded = useCallback((docId: string) => {
    setSelectedId(docId);
    refetch();
  }, [refetch]);

  const handleDelete = useCallback(async (docId: string) => {
    if (!window.confirm('Delete this document?')) return;
    try {
      await ingestApi.deleteDocument(docId);
      if (selectedId === docId) setSelectedId(null);
      if (detailId === docId) setDetailId(null);
      refetch();
    } catch {
      // ignore
    }
  }, [selectedId, detailId, refetch]);

  const handleSelect = useCallback((docId: string) => {
    setSelectedId(docId === selectedId ? null : docId);
  }, [selectedId]);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-800">Document Ingestion</h2>
        <p className="text-sm text-gray-500">Upload PDFs and monitor the processing pipeline</p>
      </div>

      <UploadForm onUploaded={handleUploaded} />

      <PipelineMonitor documentId={selectedId} />

      <div>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-700">Documents</h3>
          <span className="text-xs text-gray-400">{documents.length} documents</span>
        </div>
        <DocumentList
          documents={documents}
          isLoading={isLoading}
          selectedId={selectedId}
          onSelect={handleSelect}
          onDelete={handleDelete}
        />
      </div>

      {/* Slide-out panel for viewing chunks when double-clicking or via action */}
      {selectedId && (
        <div className="flex justify-end">
          <button
            onClick={() => setDetailId(selectedId)}
            className="rounded bg-gray-100 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-200"
          >
            View chunks for selected document
          </button>
        </div>
      )}

      <DocumentDetail documentId={detailId} onClose={() => setDetailId(null)} />
    </div>
  );
}
