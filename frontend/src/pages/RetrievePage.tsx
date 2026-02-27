import { useState } from 'react';
import QueryForm from '../components/retrieval/QueryForm';
import PipelineDecisions from '../components/retrieval/PipelineDecisions';
import RetrievalResults from '../components/retrieval/RetrievalResults';
import DocumentPreview from '../components/retrieval/DocumentPreview';
import ErrorAlert from '../components/common/ErrorAlert';
import { useRetrieval } from '../hooks/useRetrieval';
import type { RetrievalRequest } from '../types';

export default function RetrievePage() {
  const { results, isLoading, error, executeQuery } = useRetrieval();
  const [previewDocId, setPreviewDocId] = useState<string | null>(null);

  const handleSubmit = (request: RetrievalRequest) => {
    executeQuery(request);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-800">Retrieval</h2>
        <p className="text-sm text-gray-500">Search documents with hybrid retrieval and inspect pipeline decisions</p>
      </div>

      <QueryForm onSubmit={handleSubmit} isLoading={isLoading} />

      {error && <ErrorAlert message={error} />}

      {results && (
        <>
          <PipelineDecisions
            latencyBreakdown={results.latency_breakdown}
            entitiesDetected={results.entities_detected}
          />
          <RetrievalResults
            response={results}
            onViewDocument={setPreviewDocId}
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
