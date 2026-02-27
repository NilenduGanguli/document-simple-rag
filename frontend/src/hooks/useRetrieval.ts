import { useState, useCallback } from 'react';
import { retrievalApi } from '../api/retrievalApi';
import type { RetrievalRequest, RetrievalResponse } from '../types';

export function useRetrieval() {
  const [results, setResults] = useState<RetrievalResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const executeQuery = useCallback(async (request: RetrievalRequest) => {
    setIsLoading(true);
    setError(null);
    setResults(null);
    try {
      const res = await retrievalApi.retrieve(request);
      setResults(res);
    } catch (e: any) {
      setError(e.message || 'Retrieval failed');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const reset = useCallback(() => {
    setResults(null);
    setError(null);
  }, []);

  return { results, isLoading, error, executeQuery, reset };
}
