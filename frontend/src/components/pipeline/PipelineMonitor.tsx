import { useDocumentStatus } from '../../hooks/useDocumentStatus';
import PipelineDiagram from './PipelineDiagram';
import LoadingSpinner from '../common/LoadingSpinner';

interface Props {
  documentId: string | null;
}

export default function PipelineMonitor({ documentId }: Props) {
  const { document, isLoading, error } = useDocumentStatus(documentId);

  if (!documentId) return null;

  if (isLoading && !document) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-6">
        <div className="flex items-center gap-3">
          <LoadingSpinner size="sm" />
          <span className="text-sm text-gray-500">Loading pipeline status...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {error}
      </div>
    );
  }

  if (!document) return null;

  const progressPercent = document.total_chunks > 0
    ? Math.round((document.chunks_done / document.total_chunks) * 100)
    : 0;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-6">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-800">
          Pipeline: {document.filename}
        </h3>
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
          document.status === 'ready'
            ? 'bg-emerald-100 text-emerald-800'
            : document.status === 'failed'
              ? 'bg-red-100 text-red-800'
              : 'bg-blue-100 text-blue-800'
        }`}>
          {document.status}
        </span>
      </div>

      <PipelineDiagram stages={document.pipeline_stages} />

      {/* Embedding progress bar */}
      {document.status === 'embedding' && document.total_chunks > 0 && (
        <div className="mt-4">
          <div className="mb-1 flex justify-between text-xs text-gray-500">
            <span>Embedding progress</span>
            <span>{document.chunks_done}/{document.total_chunks} ({progressPercent}%)</span>
          </div>
          <div className="h-2 w-full rounded-full bg-gray-200">
            <div
              className="h-2 rounded-full bg-blue-500 transition-all duration-500"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>
      )}

      {/* Document metadata */}
      <div className="mt-4 grid grid-cols-4 gap-4 text-xs text-gray-500">
        {document.page_count != null && (
          <div>
            <span className="font-medium text-gray-700">Pages:</span> {document.page_count}
          </div>
        )}
        <div>
          <span className="font-medium text-gray-700">Chunks:</span> {document.total_chunks}
        </div>
        <div>
          <span className="font-medium text-gray-700">Embeddings:</span> {document.total_embeddings}
        </div>
        {document.file_size_bytes != null && (
          <div>
            <span className="font-medium text-gray-700">Size:</span>{' '}
            {(document.file_size_bytes / (1024 * 1024)).toFixed(1)} MB
          </div>
        )}
      </div>

      {document.error_message && document.status === 'failed' && (
        <div className="mt-3 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700">
          Error: {document.error_message}
        </div>
      )}
    </div>
  );
}
