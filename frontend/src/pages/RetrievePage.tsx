import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import QueryForm from '../components/retrieval/QueryForm';
import PipelineDecisions from '../components/retrieval/PipelineDecisions';
import RetrievalResults from '../components/retrieval/RetrievalResults';
import DocumentPreview from '../components/retrieval/DocumentPreview';
import ErrorAlert from '../components/common/ErrorAlert';
import { useRetrieval } from '../hooks/useRetrieval';
import { useDocuments } from '../hooks/useDocuments';
import type { RetrievalRequest } from '../types';

export default function RetrievePage() {
  const navigate = useNavigate();
  const { results, isLoading, error, executeQuery } = useRetrieval();
  const { documents } = useDocuments({ refreshInterval: 30000 });
  const [previewDocId, setPreviewDocId] = useState<string | null>(null);

  // Build a fast docId → filename lookup used to label chunks in k_chunks mode
  const docNameMap = useMemo<Record<string, string>>(
    () => Object.fromEntries(documents.map((d) => [d.document_id, d.filename])),
    [documents],
  );

  const handleViewDocument = (docId: string, chunkId: string) => {
    // Navigate to Explorer and auto-select the document + focus the specific chunk
    navigate(`/explore?docId=${encodeURIComponent(docId)}&chunkId=${encodeURIComponent(chunkId)}`);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-800">Retrieval</h2>
        <p className="text-sm text-gray-500">Search documents with hybrid retrieval and inspect pipeline decisions</p>
      </div>

      <QueryForm onSubmit={(request: RetrievalRequest) => executeQuery(request)} isLoading={isLoading} />

      {error && <ErrorAlert message={error} />}

      {results && (
        <>
          <PipelineDecisions
            latencyBreakdown={results.latency}
            entitiesDetected={results.entities}
          />
          <RetrievalResults
            response={results}
            docNameMap={docNameMap}
            onViewDocument={handleViewDocument}
            onViewPDF={setPreviewDocId}
          />
        </>
      )}

      <DocumentPreview
        documentId={previewDocId}
        onClose={() => setPreviewDocId(null)}
      />
    </div>
  );
}
