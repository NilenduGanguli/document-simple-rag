import { useState, useMemo } from 'react';
import { useDocuments } from '../hooks/useDocuments';
import { useSystemStats } from '../hooks/useSystemStats';
import { ingestApi } from '../api/ingestApi';
import type { DocumentSummary, ReprocessParams } from '../types';
import ReprocessModal from '../components/admin/ReprocessModal';
import ChunkManagementPanel from '../components/admin/ChunkManagementPanel';

const STATUS_COLOR: Record<string, string> = {
  ready: 'bg-emerald-100 text-emerald-700',
  pending: 'bg-yellow-100 text-yellow-700',
  ingesting: 'bg-blue-100 text-blue-700',
  chunking: 'bg-blue-100 text-blue-700',
  embedding: 'bg-purple-100 text-purple-700',
  failed: 'bg-red-100 text-red-700',
};

const STATUS_OPTIONS = ['', 'ready', 'pending', 'ingesting', 'chunking', 'embedding', 'failed'];

function formatBytes(bytes: number | null): string {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'short',
    timeStyle: 'short',
  });
}

export default function AdminPage() {
  const { documents, total, isLoading, refetch } = useDocuments({ refreshInterval: 10000 });
  const { stats } = useSystemStats();

  const [statusFilter, setStatusFilter] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedDoc, setSelectedDoc] = useState<DocumentSummary | null>(null);
  const [reprocessDoc, setReprocessDoc] = useState<DocumentSummary | null>(null);
  const [confirmDeleteDoc, setConfirmDeleteDoc] = useState<DocumentSummary | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const filtered = useMemo(() => {
    let list = documents;
    if (statusFilter) list = list.filter((d) => d.status === statusFilter);
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter((d) => d.filename.toLowerCase().includes(q));
    }
    return list;
  }, [documents, statusFilter, searchQuery]);

  const statusCounts = useMemo(
    () => stats?.documents.by_status ?? {},
    [stats],
  );

  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selectedIds.size === filtered.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filtered.map((d) => d.document_id)));
    }
  }

  function showSuccess(msg: string) {
    setActionSuccess(msg);
    setTimeout(() => setActionSuccess(null), 3000);
  }

  async function handleDelete(doc: DocumentSummary) {
    setActionError(null);
    try {
      const resp = await ingestApi.deleteDocument(doc.document_id);
      showSuccess(resp.message || `Deleted "${doc.filename}"`);
      if (selectedDoc?.document_id === doc.document_id) setSelectedDoc(null);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(doc.document_id);
        return next;
      });
      refetch();
    } catch (e: any) {
      setActionError(e.message || 'Delete failed');
    } finally {
      setConfirmDeleteDoc(null);
    }
  }

  async function handleBulkDelete() {
    setActionError(null);
    let succeeded = 0;
    for (const id of selectedIds) {
      try {
        await ingestApi.deleteDocument(id);
        succeeded++;
      } catch {
        // continue
      }
    }
    setSelectedIds(new Set());
    showSuccess(`Deleted ${succeeded} document${succeeded !== 1 ? 's' : ''}.`);
    refetch();
  }

  async function handleReprocess(doc: DocumentSummary, params: ReprocessParams) {
    await ingestApi.reprocessDocument(doc.document_id, params);
    setReprocessDoc(null);
    showSuccess(`Reprocess queued for "${doc.filename}".`);
    refetch();
  }

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Admin Control</h1>
          <p className="text-sm text-gray-500">Manage documents, chunks, and reprocessing</p>
        </div>
        <button
          onClick={refetch}
          className="rounded-md bg-gray-100 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-200"
        >
          Refresh
        </button>
      </div>

      {/* Stats bar */}
      {stats && (
        <div className="grid grid-cols-4 gap-3">
          {[
            ['Total', total, 'text-gray-900'],
            ['Ready', statusCounts['ready'] ?? 0, 'text-emerald-600'],
            ['Pending / Processing', (statusCounts['pending'] ?? 0) + (statusCounts['ingesting'] ?? 0) + (statusCounts['chunking'] ?? 0) + (statusCounts['embedding'] ?? 0), 'text-blue-600'],
            ['Failed', statusCounts['failed'] ?? 0, 'text-red-600'],
          ].map(([label, value, color]) => (
            <div key={label as string} className="rounded-lg border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">{label}</p>
              <p className={`mt-1 text-2xl font-bold ${color}`}>{value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Feedback */}
      {actionSuccess && (
        <div className="rounded-md bg-emerald-50 border border-emerald-200 px-4 py-2 text-sm text-emerald-700">
          {actionSuccess}
        </div>
      )}
      {actionError && (
        <div className="rounded-md bg-red-50 border border-red-200 px-4 py-2 text-sm text-red-700">
          {actionError}
        </div>
      )}

      <div className="flex flex-1 gap-4 min-h-0">
        {/* Left panel — document table */}
        <div className="flex w-2/3 flex-col rounded-lg border border-gray-200 bg-white">
          {/* Toolbar */}
          <div className="flex items-center gap-3 border-b border-gray-200 p-3">
            <input
              type="text"
              placeholder="Search by filename…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="flex-1 rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s === '' ? 'All statuses' : s}
                </option>
              ))}
            </select>
            {selectedIds.size > 0 && (
              <button
                onClick={handleBulkDelete}
                className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700"
              >
                Delete {selectedIds.size} selected
              </button>
            )}
          </div>

          {/* Table */}
          <div className="flex-1 overflow-y-auto">
            {isLoading && filtered.length === 0 ? (
              <div className="flex items-center justify-center py-16 text-gray-400 text-sm">
                Loading documents…
              </div>
            ) : filtered.length === 0 ? (
              <div className="flex items-center justify-center py-16 text-gray-400 text-sm">
                No documents found.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50 text-left text-xs text-gray-500">
                    <th className="w-8 px-3 py-2">
                      <input
                        type="checkbox"
                        checked={selectedIds.size === filtered.length && filtered.length > 0}
                        onChange={toggleSelectAll}
                        className="h-3.5 w-3.5 rounded border-gray-300"
                      />
                    </th>
                    <th className="px-3 py-2 font-medium">Filename</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 font-medium">Chunks</th>
                    <th className="px-3 py-2 font-medium">Size</th>
                    <th className="px-3 py-2 font-medium">Created</th>
                    <th className="px-3 py-2 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((doc) => {
                    const isSelected = selectedIds.has(doc.document_id);
                    const isActive = selectedDoc?.document_id === doc.document_id;
                    return (
                      <tr
                        key={doc.document_id}
                        onClick={() => setSelectedDoc(isActive ? null : doc)}
                        className={`cursor-pointer border-b border-gray-100 hover:bg-gray-50 ${isActive ? 'bg-blue-50' : ''}`}
                      >
                        <td
                          className="w-8 px-3 py-2"
                          onClick={(e) => { e.stopPropagation(); toggleSelect(doc.document_id); }}
                        >
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => {}}
                            className="h-3.5 w-3.5 rounded border-gray-300"
                          />
                        </td>
                        <td className="max-w-[180px] truncate px-3 py-2 font-medium text-gray-900">
                          {doc.filename}
                        </td>
                        <td className="px-3 py-2">
                          <span
                            className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                              STATUS_COLOR[doc.status] ?? 'bg-gray-100 text-gray-600'
                            }`}
                          >
                            {doc.status}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-gray-600">{doc.chunk_count}</td>
                        <td className="px-3 py-2 text-gray-500">{formatBytes(doc.file_size_bytes)}</td>
                        <td className="px-3 py-2 text-gray-500">{formatDate(doc.created_at)}</td>
                        <td
                          className="px-3 py-2"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => setReprocessDoc(doc)}
                              className="rounded px-2 py-1 text-xs text-blue-600 hover:bg-blue-50"
                            >
                              Reprocess
                            </button>
                            <button
                              onClick={() => setConfirmDeleteDoc(doc)}
                              className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                            >
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          <div className="border-t border-gray-200 px-4 py-2 text-xs text-gray-400">
            {filtered.length} of {total} document{total !== 1 ? 's' : ''}
          </div>
        </div>

        {/* Right panel — document detail + chunks */}
        <div className="flex w-1/3 flex-col rounded-lg border border-gray-200 bg-white">
          {selectedDoc ? (
            <>
              <div className="border-b border-gray-200 p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="truncate text-sm font-semibold text-gray-900">
                      {selectedDoc.filename}
                    </h2>
                    <p className="mt-0.5 text-xs text-gray-400">
                      {selectedDoc.document_id.slice(0, 8)}…
                    </p>
                  </div>
                  <button
                    onClick={() => setSelectedDoc(null)}
                    className="text-gray-400 hover:text-gray-600 flex-shrink-0"
                  >
                    <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                      <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
                    </svg>
                  </button>
                </div>

                {/* Meta */}
                <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  {[
                    ['Status', selectedDoc.status],
                    ['Chunks', selectedDoc.chunk_count],
                    ['Size', formatBytes(selectedDoc.file_size_bytes)],
                    ['Pages', selectedDoc.page_count ?? '—'],
                    ['Created', formatDate(selectedDoc.created_at)],
                    ['Updated', formatDate(selectedDoc.updated_at)],
                  ].map(([k, v]) => (
                    <div key={k as string}>
                      <dt className="text-gray-400">{k}</dt>
                      <dd className="font-medium text-gray-700 truncate">{String(v)}</dd>
                    </div>
                  ))}
                </dl>

                {selectedDoc.error_message && selectedDoc.error_message !== 'deleted' && (
                  <p className="mt-2 rounded bg-red-50 px-2 py-1 text-xs text-red-700">
                    {selectedDoc.error_message}
                  </p>
                )}

                {/* Actions */}
                <div className="mt-3 flex gap-2">
                  <button
                    onClick={() => setReprocessDoc(selectedDoc)}
                    className="flex-1 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
                  >
                    Reprocess
                  </button>
                  <button
                    onClick={() => setConfirmDeleteDoc(selectedDoc)}
                    className="flex-1 rounded-md bg-red-50 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-100"
                  >
                    Delete
                  </button>
                </div>
              </div>

              {/* Chunk panel */}
              <div className="flex-1 overflow-y-auto p-4">
                <h3 className="mb-3 text-xs font-medium uppercase tracking-wide text-gray-400">
                  Chunks
                </h3>
                <ChunkManagementPanel document={selectedDoc} />
              </div>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-gray-400 text-sm">
              Select a document to view details
            </div>
          )}
        </div>
      </div>

      {/* Reprocess modal */}
      {reprocessDoc && (
        <ReprocessModal
          document={reprocessDoc}
          onConfirm={(params) => handleReprocess(reprocessDoc, params)}
          onCancel={() => setReprocessDoc(null)}
        />
      )}

      {/* Delete confirmation */}
      {confirmDeleteDoc && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-sm rounded-lg bg-white p-6 shadow-xl">
            <h2 className="text-base font-semibold text-gray-900">Delete Document?</h2>
            <p className="mt-2 text-sm text-gray-600 break-all">{confirmDeleteDoc.filename}</p>
            <p className="mt-2 text-sm text-gray-500">
              This will soft-delete the document and permanently remove all{' '}
              <strong>{confirmDeleteDoc.chunk_count}</strong> chunk
              {confirmDeleteDoc.chunk_count !== 1 ? 's' : ''} from the database.
            </p>
            <div className="mt-5 flex justify-end gap-3">
              <button
                onClick={() => setConfirmDeleteDoc(null)}
                className="rounded-md px-4 py-2 text-sm text-gray-600 hover:bg-gray-100"
              >
                Cancel
              </button>
              <button
                onClick={() => handleDelete(confirmDeleteDoc)}
                className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
