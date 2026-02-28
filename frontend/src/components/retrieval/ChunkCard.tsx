import { useState } from 'react';
import type { ChunkResult } from '../../types';

interface Props {
  chunk: ChunkResult;
  query: string;
  /** Parent document filename — shown in k_chunks mode where docs are not grouped */
  filename?: string;
  /** Navigate to Explorer and focus this chunk */
  onViewDocument: (docId: string, chunkId: string) => void;
  /** Open the PDF preview for the parent document */
  onViewPDF?: (docId: string) => void;
}

export default function ChunkCard({ chunk, query, filename, onViewDocument, onViewPDF }: Props) {
  const [expanded, setExpanded] = useState(false);

  // Simple highlight: wrap query terms in the chunk text
  const highlightText = (text: string) => {
    if (!query.trim()) return text;
    const words = query.trim().split(/\s+/).filter(w => w.length > 2);
    if (words.length === 0) return text;
    const regex = new RegExp(`(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
    const parts = text.split(regex);
    return parts.map((part, i) =>
      regex.test(part) ? (
        <mark key={i} className="bg-yellow-200 text-yellow-900 rounded px-0.5">{part}</mark>
      ) : (
        <span key={i}>{part}</span>
      )
    );
  };

  const displayText = expanded ? chunk.chunk_text : chunk.chunk_text.slice(0, 300);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 transition-shadow hover:shadow-md">
      {/* Parent document name (k_chunks mode) */}
      {filename && (
        <div className="mb-2 flex items-center gap-1.5 truncate text-xs">
          <svg className="h-3.5 w-3.5 flex-shrink-0 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span className="truncate font-medium text-gray-600" title={filename}>{filename}</span>
        </div>
      )}

      {/* Header with scores */}
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-gray-600">
          #{chunk.chunk_index}
        </span>
        {chunk.page_number != null && (
          <span className="text-gray-400">pg. {chunk.page_number}</span>
        )}
        <span className="text-gray-400">|</span>
        <span className="text-gray-500" title="Cosine similarity">
          cos: <span className="font-medium text-blue-600">{chunk.cosine_score.toFixed(3)}</span>
        </span>
        <span className="text-gray-500" title="BM25 score">
          bm25: <span className="font-medium text-indigo-600">{chunk.bm25_score.toFixed(2)}</span>
        </span>
        <span className="text-gray-500" title="RRF fused score">
          rrf: <span className="font-medium text-purple-600">{chunk.rrf_score.toFixed(4)}</span>
        </span>
        {chunk.rerank_score != null && (
          <span className="text-gray-500" title="Cross-encoder rerank score">
            rerank: <span className="font-medium text-emerald-600">{chunk.rerank_score.toFixed(4)}</span>
          </span>
        )}
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${
          chunk.source_type === 'ocr' ? 'bg-orange-100 text-orange-700' : 'bg-gray-100 text-gray-600'
        }`}>
          {chunk.source_type}
        </span>
      </div>

      {/* Chunk text */}
      <div className="text-sm leading-relaxed text-gray-800">
        {highlightText(displayText)}
        {!expanded && chunk.chunk_text.length > 300 && (
          <span className="text-gray-400">...</span>
        )}
      </div>

      {/* Actions */}
      <div className="mt-3 flex items-center gap-3">
        {chunk.chunk_text.length > 300 && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-blue-600 hover:text-blue-700"
          >
            {expanded ? 'Show less' : 'Show more'}
          </button>
        )}
        {onViewPDF && (
          <button
            onClick={() => onViewPDF(chunk.parent_document_id)}
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            View parent document
          </button>
        )}
        <button
          onClick={() => onViewDocument(chunk.parent_document_id, chunk.chunk_id)}
          className="text-xs text-gray-500 hover:text-gray-700"
        >
          View in Explorer ↗
        </button>
        <span className="ml-auto text-[10px] font-mono text-gray-300" title="Chunk ID">
          {chunk.chunk_id.slice(0, 8)}...
        </span>
      </div>
    </div>
  );
}
