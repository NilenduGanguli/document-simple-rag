const statusColors: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-800',
  ingesting: 'bg-blue-100 text-blue-800',
  chunking: 'bg-indigo-100 text-indigo-800',
  embedding: 'bg-purple-100 text-purple-800',
  ready: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-red-100 text-red-800',
  duplicate: 'bg-gray-100 text-gray-800',
  accepted: 'bg-blue-100 text-blue-800',
  deleted: 'bg-gray-100 text-gray-500',
  done: 'bg-emerald-100 text-emerald-800',
  processing: 'bg-blue-100 text-blue-800',
};

export default function StatusBadge({ status }: { status: string }) {
  const color = statusColors[status] || 'bg-gray-100 text-gray-700';
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${color}`}>
      {status}
    </span>
  );
}
