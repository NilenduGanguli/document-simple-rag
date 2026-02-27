import { useState, useEffect, useCallback, useRef } from 'react';
import { retrievalApi } from '../api/retrievalApi';
import type { DocumentSummary } from '../types';

export function useDocuments(opts: { status?: string; refreshInterval?: number } = {}) {
  const { status, refreshInterval = 5000 } = opts;
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  const fetch = useCallback(async () => {
    try {
      const res = await retrievalApi.listDocuments({ limit: 100, status });
      setDocuments(res.documents);
      setTotal(res.total);
      setError(null);
    } catch (e: any) {
      setError(e.message || 'Failed to fetch documents');
    } finally {
      setIsLoading(false);
    }
  }, [status]);

  useEffect(() => {
    fetch();
    intervalRef.current = setInterval(fetch, refreshInterval);
    return () => clearInterval(intervalRef.current);
  }, [fetch, refreshInterval]);

  return { documents, total, isLoading, error, refetch: fetch };
}
