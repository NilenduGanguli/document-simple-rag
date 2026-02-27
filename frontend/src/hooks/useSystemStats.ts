import { useState, useEffect, useRef } from 'react';
import { retrievalApi } from '../api/retrievalApi';
import type { SystemStats } from '../types';

export function useSystemStats(refreshInterval = 10000) {
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await retrievalApi.getStats();
        setStats(res);
        setError(null);
      } catch (e: any) {
        setError(e.message || 'Failed to fetch stats');
      } finally {
        setIsLoading(false);
      }
    };

    fetchStats();
    intervalRef.current = setInterval(fetchStats, refreshInterval);
    return () => clearInterval(intervalRef.current);
  }, [refreshInterval]);

  return { stats, isLoading, error };
}
