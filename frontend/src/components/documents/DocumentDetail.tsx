import { useState, useEffect } from 'react';
import { retrievalApi } from '../../api/retrievalApi';
import type { ChunkItem } from '../../types';
import StatusBadge from '../common/StatusBadge';
import LoadingSpinner from '../common/LoadingSpinner';

interface Props {
  documentId: string | null;
  onClose: () => void;
}

export default function DocumentDetail({ documentId, onClose }: Props) {
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [totalChunks, setTotalChunks] = useState(0);
  const [loading, setLoading] = useState(false);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!documentId) return;
    setLoading(true);
    retrievalApi.getChunks(documentId, { limit: 20 }).then((res) => {
      setChunks(res.chunks);
      setTotalChunks(res.total_chunks);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [documentId]);

  const handleDownload = async () => {
    if (!documentId) return;
    try {
      const res = await retrievalApi.getDownloadUrl(documentId);
      setDownloadUrl(res.url);
      window.open(res.url, '_blank');
    } catch {
      // ignore
    }
  };

  if (!documentId) return null;

  return (
    <div className="fixed inset-y-0 right-0 z-50 flex w-[480px] flex-col border-l border-gray-200 bg-white shadow-xl">
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
        <h3 className="text-sm font-semibold text-gray-800">Chunks Preview</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={handleDownload}
            className="rounded bg-gray-100 px-2 py-1 text-xs text-gray-700 hover:bg-gray-200"
          >
            Download PDF
          </button>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <p className="mb-3 text-xs text-gray-400">{totalChunks} chunks total (showing first 20)</p>

        {loading ? (
          <div className="flex justify-center py-8"><LoadingSpinner /></div>
        ) : (
          <div className="space-y-3">
            {chunks.map((chunk) => (
              <div key={chunk.chunk_id} className="rounded-lg border border-gray-200 p-3">
                <div className="mb-2 flex items-center gap-2 text-xs text-gray-500">
                  <span className="font-medium">#{chunk.chunk_index}</span>
                  {chunk.page_number != null && <span>pg. {chunk.page_number}</span>}
                  <StatusBadge status={chunk.embedding_status} />
                  {chunk.token_count != null && <span>{chunk.token_count} tokens</span>}
                </div>
                <p className="text-xs leading-relaxed text-gray-700 line-clamp-4">
                  {chunk.chunk_text}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
