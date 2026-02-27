import { useState, useEffect, useRef } from 'react';
import { retrievalApi } from '../api/retrievalApi';
import type { DocumentPipelineStatus } from '../types';

export function useDocumentStatus(documentId: string | null) {
  const [document, setDocument] = useState<DocumentPipelineStatus | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    if (!documentId) {
      setDocument(null);
      return;
    }

    let cancelled = false;
    setIsLoading(true);

    const fetchStatus = async () => {
      try {
        const res = await retrievalApi.getDocumentPipeline(documentId);
        if (!cancelled) {
          setDocument(res);
          setError(null);
          // Stop polling on terminal states
          if (res.status === 'ready' || res.status === 'failed') {
            clearInterval(intervalRef.current);
          }
        }
      } catch (e: any) {
        if (!cancelled) setError(e.message || 'Failed to fetch document status');
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    fetchStatus();
    intervalRef.current = setInterval(fetchStatus, 2000);

    return () => {
      cancelled = true;
      clearInterval(intervalRef.current);
    };
  }, [documentId]);

  return { document, isLoading, error };
}
