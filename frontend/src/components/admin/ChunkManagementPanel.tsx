import { useState } from 'react';
import type { DocumentSummary, ChunkItem } from '../../types';
import { retrievalApi } from '../../api/retrievalApi';

interface Props {
  document: DocumentSummary;
}

const EMBEDDING_STATUS_COLOR: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-700',
  processing: 'bg-blue-100 text-blue-700',
  done: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
};

export default function ChunkManagementPanel({ document: doc }: Props) {
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  async function loadChunks() {
    setLoading(true);
    setError(null);
    try {
      const res = await retrievalApi.getChunks(doc.document_id, { limit: 200, offset: 0 });
      setChunks(res.chunks);
      setTotal(res.total_chunks);
      setLoaded(true);
    } catch (e: any) {
      setError(e.message || 'Failed to load chunks');
    } finally {
      setLoading(false);
    }
  }

  function toggleExpand(chunkId: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.has(chunkId) ? next.delete(chunkId) : next.add(chunkId);
      return next;
    });
  }

  if (!loaded) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-gray-400">
        <p className="mb-4 text-sm">
          {doc.chunk_count > 0
            ? `${doc.chunk_count} chunk${doc.chunk_count !== 1 ? 's' : ''} stored`
            : 'No chunks yet'}
        </p>
        <button
          onClick={loadChunks}
          disabled={loading || doc.chunk_count === 0}
          className="rounded-md bg-gray-100 px-4 py-2 text-sm text-gray-700 hover:bg-gray-200 disabled:opacity-40"
        >
          {loading ? 'Loading…' : 'Load Chunks'}
        </button>
        {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-700">
          {total} chunk{total !== 1 ? 's' : ''}
        </h3>
        <button
          onClick={loadChunks}
          className="text-xs text-blue-600 hover:underline"
        >
          Refresh
        </button>
      </div>

      {chunks.length === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400">No chunks found.</p>
      ) : (
        <div className="space-y-2">
          {chunks.map((chunk) => {
            const expanded = expandedIds.has(chunk.chunk_id);
            const statusColor =
              EMBEDDING_STATUS_COLOR[chunk.embedding_status] ?? 'bg-gray-100 text-gray-600';
            return (
              <div
                key={chunk.chunk_id}
                className="rounded-md border border-gray-200 bg-white p-3"
              >
                <button
                  onClick={() => toggleExpand(chunk.chunk_id)}
                  className="flex w-full items-center justify-between text-left"
                >
                  <div className="flex items-center gap-2 text-xs text-gray-500">
                    <span className="font-mono text-gray-400">#{chunk.chunk_index}</span>
                    {chunk.token_count != null && (
                      <span>{chunk.token_count} tok</span>
                    )}
                    {chunk.page_number != null && (
                      <span>p.{chunk.page_number + 1}</span>
                    )}
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${statusColor}`}>
                      {chunk.embedding_status}
                    </span>
                  </div>
                  <svg
                    className={`h-3 w-3 flex-shrink-0 text-gray-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
                    viewBox="0 0 20 20"
                    fill="currentColor"
                  >
                    <path
                      fillRule="evenodd"
                      d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z"
                      clipRule="evenodd"
                    />
                  </svg>
                </button>
                {expanded && (
                  <p className="mt-2 whitespace-pre-wrap text-xs text-gray-700 leading-relaxed">
                    {chunk.chunk_text}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
