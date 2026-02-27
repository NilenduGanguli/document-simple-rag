import { useSystemStats } from '../hooks/useSystemStats';
import { useDocuments } from '../hooks/useDocuments';
import LoadingSpinner from '../components/common/LoadingSpinner';
import StatusBadge from '../components/common/StatusBadge';

const statusColors: Record<string, string> = {
  pending: 'bg-yellow-400',
  ingesting: 'bg-blue-400',
  chunking: 'bg-indigo-400',
  embedding: 'bg-purple-400',
  ready: 'bg-emerald-400',
  failed: 'bg-red-400',
};

export default function DashboardPage() {
  const { stats, isLoading: statsLoading } = useSystemStats();
  const { documents } = useDocuments({ refreshInterval: 10000 });

  if (statsLoading && !stats) {
    return (
      <div className="flex items-center justify-center py-20">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  const recentDocs = documents.slice(0, 5);
  const maxStatusCount = stats
    ? Math.max(1, ...Object.values(stats.documents.by_status))
    : 1;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-800">Dashboard</h2>
        <p className="text-sm text-gray-500">System overview and statistics</p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard label="Documents" value={stats?.documents.total ?? 0} />
        <StatCard label="Chunks" value={stats?.chunks.total ?? 0} />
        <StatCard label="Queries (24h)" value={stats?.retrieval.queries_last_24h ?? 0} />
        <StatCard
          label="Avg Latency"
          value={stats?.retrieval.avg_latency_ms != null ? `${stats.retrieval.avg_latency_ms.toFixed(0)}ms` : '-'}
        />
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Documents by status */}
        <div className="rounded-lg border border-gray-200 bg-white p-5">
          <h3 className="mb-4 text-sm font-semibold text-gray-700">Documents by Status</h3>
          {stats && (
            <div className="space-y-3">
              {Object.entries(stats.documents.by_status).map(([status, count]) => (
                <div key={status} className="flex items-center gap-3">
                  <span className="w-20 text-xs text-gray-600">{status}</span>
                  <div className="flex-1">
                    <div className="h-5 rounded-full bg-gray-100">
                      <div
                        className={`h-5 rounded-full ${statusColors[status] || 'bg-gray-400'} transition-all duration-500`}
                        style={{ width: `${(count / maxStatusCount) * 100}%`, minWidth: count > 0 ? '24px' : '0' }}
                      />
                    </div>
                  </div>
                  <span className="w-8 text-right text-xs font-medium text-gray-700">{count}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Chunk stats */}
        <div className="rounded-lg border border-gray-200 bg-white p-5">
          <h3 className="mb-4 text-sm font-semibold text-gray-700">Chunk & Embedding Stats</h3>
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-gray-500">Total chunks</span>
              <span className="font-medium">{stats?.chunks.total ?? 0}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-gray-500">Total embeddings</span>
              <span className="font-medium">{stats?.chunks.total_embeddings ?? 0}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-gray-500">BM25 index size</span>
              <span className="font-medium">{stats?.bm25.index_size ?? 0}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-gray-500">Total queries</span>
              <span className="font-medium">{stats?.retrieval.total_queries ?? 0}</span>
            </div>
            {stats?.chunks.by_embedding_status && (
              <div className="mt-2 border-t border-gray-100 pt-2">
                <span className="text-xs font-medium text-gray-400">Embedding status breakdown:</span>
                {Object.entries(stats.chunks.by_embedding_status).map(([status, count]) => (
                  <div key={status} className="mt-1 flex items-center justify-between text-xs">
                    <StatusBadge status={status} />
                    <span className="text-gray-600">{count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Recent documents */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <h3 className="mb-4 text-sm font-semibold text-gray-700">Recent Documents</h3>
        {recentDocs.length === 0 ? (
          <p className="text-sm text-gray-400">No documents yet.</p>
        ) : (
          <div className="space-y-2">
            {recentDocs.map((doc) => (
              <div key={doc.document_id} className="flex items-center justify-between rounded-md bg-gray-50 px-3 py-2">
                <div className="flex items-center gap-3">
                  <span className="text-sm text-gray-800 truncate max-w-[250px]">{doc.filename}</span>
                  <StatusBadge status={doc.status} />
                </div>
                <div className="flex items-center gap-4 text-xs text-gray-400">
                  <span>{doc.chunk_count} chunks</span>
                  {doc.created_at && (
                    <span>{new Date(doc.created_at).toLocaleDateString()}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-5">
      <p className="text-xs font-medium uppercase tracking-wider text-gray-400">{label}</p>
      <p className="mt-2 text-2xl font-bold text-gray-800">{value}</p>
    </div>
  );
}
