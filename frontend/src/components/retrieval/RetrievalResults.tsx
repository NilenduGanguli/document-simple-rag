import type { RetrievalResponse } from '../../types';
import ChunkCard from './ChunkCard';

interface Props {
  response: RetrievalResponse;
  /** docId → filename lookup — used to show filename on chunks in k_chunks mode */
  docNameMap: Record<string, string>;
  /** Navigate to Explorer and focus this chunk */
  onViewDocument: (docId: string, chunkId: string) => void;
  /** Open the PDF preview for this document (n_documents mode only) */
  onViewPDF: (docId: string) => void;
}

export default function RetrievalResults({ response, docNameMap, onViewDocument, onViewPDF }: Props) {
  if (response.mode === 'k_chunks' && response.results_k_chunks) {
    return (
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h4 className="text-sm font-semibold text-gray-700">
            {response.total_results} chunk{response.total_results !== 1 ? 's' : ''} retrieved
          </h4>
          <span className="text-xs text-gray-400">audit: {response.audit_id.slice(0, 8)}</span>
        </div>
        <div className="space-y-3">
          {response.results_k_chunks.map((chunk, i) => (
            <ChunkCard
              key={chunk.chunk_id || i}
              chunk={chunk}
              query={response.query}
              filename={docNameMap[chunk.parent_document_id]}
              onViewDocument={onViewDocument}
              onViewPDF={onViewPDF}
            />
          ))}
        </div>
      </div>
    );
  }

  if (response.mode === 'n_documents' && response.results_n_documents) {
    return (
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h4 className="text-sm font-semibold text-gray-700">
            {response.total_results} document{response.total_results !== 1 ? 's' : ''} retrieved
          </h4>
          <span className="text-xs text-gray-400">audit: {response.audit_id.slice(0, 8)}</span>
        </div>
        <div className="space-y-6">
          {response.results_n_documents.map((doc) => (
            <div key={doc.parent_document_id} className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h5 className="text-sm font-medium text-gray-800">{doc.filename}</h5>
                  <span className="text-xs text-gray-400">Score: {doc.document_score.toFixed(4)}</span>
                </div>
                <button
                  onClick={() => onViewPDF(doc.parent_document_id)}
                  className="rounded bg-gray-100 px-2 py-1 text-xs text-gray-600 hover:bg-gray-200"
                >
                  View PDF
                </button>
              </div>
              <div className="mb-2">
                <span className="text-xs font-medium text-gray-500">Primary chunk:</span>
              </div>
              <ChunkCard
                chunk={doc.primary_chunk}
                query={response.query}
                onViewDocument={onViewDocument}
                onViewPDF={onViewPDF}
              />
              {doc.supporting_chunks.length > 0 && (
                <div className="mt-3">
                  <span className="text-xs font-medium text-gray-400">
                    + {doc.supporting_chunks.length} supporting chunk{doc.supporting_chunks.length !== 1 ? 's' : ''}
                  </span>
                  <div className="mt-2 space-y-2">
                    {doc.supporting_chunks.map((chunk, i) => (
                      <ChunkCard
                        key={chunk.chunk_id || i}
                        chunk={chunk}
                        query={response.query}
                        onViewDocument={onViewDocument}
                        onViewPDF={onViewPDF}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return <p className="text-sm text-gray-400">No results.</p>;
}
