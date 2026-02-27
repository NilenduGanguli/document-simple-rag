import { useState, useEffect } from 'react';
import { retrievalApi } from '../../api/retrievalApi';
import LoadingSpinner from '../common/LoadingSpinner';

interface Props {
  documentId: string | null;
  onClose: () => void;
}

export default function DocumentPreview({ documentId, onClose }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [filename, setFilename] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!documentId) {
      setUrl(null);
      return;
    }
    setLoading(true);
    setError(null);
    retrievalApi.getDownloadUrl(documentId).then((res) => {
      setUrl(res.url);
      setFilename(res.filename);
      setLoading(false);
    }).catch((e) => {
      setError(e.message || 'Failed to get download URL');
      setLoading(false);
    });
  }, [documentId]);

  if (!documentId) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="flex h-[85vh] w-[70vw] flex-col rounded-lg bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <h3 className="text-sm font-semibold text-gray-800">
            {filename || 'Document Preview'}
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          {loading && (
            <div className="flex h-full items-center justify-center">
              <LoadingSpinner size="lg" />
            </div>
          )}
          {error && (
            <div className="flex h-full items-center justify-center">
              <p className="text-sm text-red-600">{error}</p>
            </div>
          )}
          {url && !loading && (
            <iframe
              src={url}
              className="h-full w-full border-0"
              title="Document Preview"
            />
          )}
        </div>
      </div>
    </div>
  );
}
