/**
 * ExplorePage — Browse ingested documents, inspect chunks, and visualise
 * the chunking pipeline decisions.
 *
 * Layout:
 *   ┌──────────────┬─────────────────────────────────────────────┐
 *   │  Document    │  Header: filename · status · metadata       │
 *   │  list        │  Ingestion Pipeline Diagram                 │
 *   │              ├─────────────────────────────────────────────┤
 *   │  (filter by  │  [Chunks (N)]  [Process & Decisions]        │
 *   │   filename)  │                                             │
 *   │              │  Chunks tab:  scrollable ChunkDetailCards   │
 *   │              │  Process tab: ChunkProcessView (SVG charts) │
 *   └──────────────┴─────────────────────────────────────────────┘
 */
import { useState, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useDocuments } from '../hooks/useDocuments';
import { useDocumentStatus } from '../hooks/useDocumentStatus';
import { retrievalApi } from '../api/retrievalApi';
import type { ChunkItem } from '../types';
import PipelineDiagram from '../components/pipeline/PipelineDiagram';
import StatusBadge from '../components/common/StatusBadge';
import LoadingSpinner from '../components/common/LoadingSpinner';
import ChunkProcessView from '../components/explore/ChunkProcessView';

const MAX_TOKENS = 512;

const SOURCE_TYPE_COLORS: Record<string, string> = {
  text:  'bg-blue-100 text-blue-700',
  ocr:   'bg-amber-100 text-amber-700',
  image: 'bg-violet-100 text-violet-700',
  mixed: 'bg-gray-100 text-gray-600',
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtBytes(b: number): string {
  if (b < 1024)       return `${b} B`;
  if (b < 1048576)    return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1048576).toFixed(2)} MB`;
}

function fmtDate(s: string | null): string {
  if (!s) return '—';
  return new Date(s).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
}

// ── ChunkDetailCard ───────────────────────────────────────────────────────────

function ChunkDetailCard({
  chunk,
  totalChunks,
  expanded,
  highlighted,
  onToggle,
}: {
  chunk: ChunkItem;
  totalChunks: number;
  expanded: boolean;
  highlighted: boolean;
  onToggle: () => void;
}) {
  const tok      = chunk.token_count ?? 0;
  const pct      = Math.min(100, Math.round((tok / MAX_TOKENS) * 100));
  const posPct   = totalChunks > 1 ? Math.round((chunk.chunk_index / (totalChunks - 1)) * 100) : 0;
  const barColor = pct >= 90 ? 'bg-red-400' : pct >= 60 ? 'bg-blue-400' : 'bg-emerald-400';

  // Infer one-line chunking insight
  let insight: string | null = null;
  if (tok >= 490)      insight = 'Max-size split — hit token limit';
  else if (tok < 80)   insight = 'Short tail — end of section or heading';

  return (
    <div
      id={`chunk-${chunk.chunk_id}`}
      className={`rounded-lg border bg-white overflow-hidden transition-shadow ${
        highlighted ? 'border-blue-400 shadow-md shadow-blue-100' : 'border-gray-200'
      }`}
    >
      {/* ── row 1: indices & badges ── */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b border-gray-100">
        <span className="font-mono text-xs font-bold text-gray-600 min-w-[2.2rem]">
          #{chunk.chunk_index}
        </span>
        {chunk.page_number != null && (
          <span className="text-xs text-gray-500">pg.{chunk.page_number}</span>
        )}
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${SOURCE_TYPE_COLORS[chunk.source_type] ?? 'bg-gray-100 text-gray-600'}`}>
          {chunk.source_type}
        </span>
        <StatusBadge status={chunk.embedding_status} />
        {insight && (
          <span className="rounded bg-orange-50 px-2 py-0.5 text-xs text-orange-600">
            {insight}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2 text-xs text-gray-400">
          <span>{tok} tok</span>
          <span className="text-gray-300">·</span>
          <span className="font-mono">{chunk.chunk_id.slice(0, 8)}…</span>
          <button
            onClick={onToggle}
            className="ml-1 text-blue-500 hover:text-blue-700 font-medium"
          >
            {expanded ? '↑ Collapse' : '↓ Expand'}
          </button>
        </div>
      </div>

      {/* ── row 2: visual metrics ── */}
      <div className="flex flex-wrap items-center gap-5 px-4 py-2 bg-gray-50 text-xs text-gray-500">
        {/* Token utilisation */}
        <div className="flex items-center gap-2 min-w-[160px]">
          <span className="whitespace-nowrap text-gray-500">Tokens</span>
          <div className="flex-1 h-2 min-w-[80px] max-w-[120px] bg-gray-200 rounded-full overflow-hidden">
            <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
          </div>
          <span className="font-semibold text-gray-600 tabular-nums">{pct}%</span>
        </div>
        {/* Document position */}
        <div className="flex items-center gap-2 min-w-[160px]">
          <span className="whitespace-nowrap text-gray-500">Position</span>
          <div className="flex-1 h-2 min-w-[80px] max-w-[120px] bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-indigo-300 rounded-full"
              style={{ width: `${Math.max(posPct, posPct > 0 ? 3 : 0)}%` }}
            />
          </div>
          <span className="font-semibold text-gray-600 tabular-nums">{posPct}%</span>
        </div>
        <span className="text-gray-400 text-[10px]">
          chunk {chunk.chunk_index + 1} / {totalChunks}
        </span>
      </div>

      {/* ── row 3: text ── */}
      <div className="px-4 py-3">
        <p className={`text-xs leading-relaxed text-gray-700 whitespace-pre-wrap break-words ${expanded ? '' : 'line-clamp-3'}`}>
          {chunk.chunk_text}
        </p>
        {!expanded && chunk.chunk_text.length > 250 && (
          <button onClick={onToggle} className="mt-1 text-xs text-blue-500 hover:text-blue-700">
            Show full text ({chunk.chunk_text.length.toLocaleString()} chars)
          </button>
        )}
      </div>
    </div>
  );
}

// ── ExplorePage ───────────────────────────────────────────────────────────────

export default function ExplorePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { documents, isLoading: docsLoading } = useDocuments({ refreshInterval: 15000 });

  const [selectedDocId,  setSelectedDocId]  = useState<string | null>(null);
  const [docSearch,      setDocSearch]      = useState('');
  const [chunkSearch,    setChunkSearch]    = useState('');
  const [activeTab,      setActiveTab]      = useState<'chunks' | 'process'>('chunks');
  const [chunks,         setChunks]         = useState<ChunkItem[]>([]);
  const [chunksLoading,  setChunksLoading]  = useState(false);
  const [expandedIds,    setExpandedIds]    = useState<Set<string>>(new Set());
  const [focusedChunkId, setFocusedChunkId] = useState<string | null>(null);
  // Deferred chunk focus: set when navigating here from Retrieve page.
  // Applied once chunks finish loading so the DOM element exists.
  const [pendingChunkId, setPendingChunkId] = useState<string | null>(null);

  const { document: docStatus } = useDocumentStatus(selectedDocId);

  // Fetch chunks whenever the selected document changes
  useEffect(() => {
    if (!selectedDocId) { setChunks([]); return; }
    setChunksLoading(true);
    setExpandedIds(new Set());
    setFocusedChunkId(null);
    setChunkSearch('');
    retrievalApi
      .getChunks(selectedDocId, { limit: 500 })
      .then((res) => setChunks(res.chunks))
      .catch(() => setChunks([]))
      .finally(() => setChunksLoading(false));
  }, [selectedDocId]);

  // Consume docId / chunkId URL params coming from the Retrieve page.
  // We wait until documents are loaded before selecting so the list item can highlight.
  useEffect(() => {
    const docId   = searchParams.get('docId');
    const chunkId = searchParams.get('chunkId');
    if (!docId) return;
    setSelectedDocId(docId);
    if (chunkId) setPendingChunkId(chunkId);
    // Remove params from URL without adding to browser history
    setSearchParams({}, { replace: true });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  // Once the chunks for the target document have loaded, scroll to the pending chunk.
  useEffect(() => {
    if (chunksLoading || !pendingChunkId || chunks.length === 0) return;
    const id = pendingChunkId;
    setFocusedChunkId(id);
    setActiveTab('chunks');
    setExpandedIds(new Set([id]));
    setPendingChunkId(null);
    setTimeout(() => {
      document.getElementById(`chunk-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 80);
  }, [chunksLoading, chunks, pendingChunkId]);

  const filteredDocs = useMemo(
    () => documents.filter((d) => d.filename.toLowerCase().includes(docSearch.toLowerCase())),
    [documents, docSearch],
  );

  const filteredChunks = useMemo(
    () =>
      chunkSearch
        ? chunks.filter((c) => c.chunk_text.toLowerCase().includes(chunkSearch.toLowerCase()))
        : chunks,
    [chunks, chunkSearch],
  );

  const toggleExpand = (id: string) =>
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // When user clicks a chunk block in the Process tab, switch to Chunks tab + scroll to it
  const handleChunkFocus = (id: string) => {
    setFocusedChunkId(id);
    setActiveTab('chunks');
    setExpandedIds(new Set([id]));
    // Scroll to the card after the tab re-renders
    setTimeout(() => {
      document.getElementById(`chunk-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 80);
  };

  return (
    // Negative margin to fill the scrollable main pane edge-to-edge
    <div className="flex -m-6 h-[calc(100vh-3.5rem)] overflow-hidden">

      {/* ─── Left: Document list ───────────────────────────────────────────── */}
      <aside className="flex w-72 flex-shrink-0 flex-col border-r border-gray-200 bg-white">
        <div className="border-b border-gray-200 px-4 py-4">
          <h2 className="text-sm font-semibold text-gray-800">Explore</h2>
          <p className="mt-0.5 text-xs text-gray-400">Browse ingested documents and inspect chunks</p>
          <input
            type="text"
            value={docSearch}
            onChange={(e) => setDocSearch(e.target.value)}
            placeholder="Search documents…"
            className="mt-3 w-full rounded border border-gray-300 px-2.5 py-1.5 text-xs focus:border-blue-500 focus:outline-none"
          />
          {filteredDocs.length > 0 && (
            <p className="mt-1.5 text-xs text-gray-400">
              {filteredDocs.length} document{filteredDocs.length !== 1 ? 's' : ''}
            </p>
          )}
        </div>

        <nav className="flex-1 overflow-y-auto">
          {docsLoading && documents.length === 0 ? (
            <div className="flex justify-center py-10"><LoadingSpinner /></div>
          ) : filteredDocs.length === 0 ? (
            <p className="px-4 py-6 text-xs text-gray-400">No documents found.</p>
          ) : (
            filteredDocs.map((doc) => {
              const active = doc.document_id === selectedDocId;
              return (
                <button
                  key={doc.document_id}
                  onClick={() => setSelectedDocId(active ? null : doc.document_id)}
                  className={`w-full border-b border-gray-100 px-4 py-3 text-left transition-colors hover:bg-gray-50 ${
                    active ? 'bg-blue-50 border-l-4 border-l-blue-500' : 'border-l-4 border-l-transparent'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0 break-all text-xs font-medium leading-snug text-gray-800 line-clamp-2">
                      {doc.filename}
                    </span>
                    <StatusBadge status={doc.status} />
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-gray-400">
                    <span>{doc.chunk_count} chunks</span>
                    {doc.page_count   != null && <span>{doc.page_count} pg</span>}
                    {doc.file_size_bytes != null && <span>{fmtBytes(doc.file_size_bytes)}</span>}
                  </div>
                  {doc.created_at && (
                    <p className="mt-0.5 text-[11px] text-gray-300">{fmtDate(doc.created_at)}</p>
                  )}
                </button>
              );
            })
          )}
        </nav>
      </aside>

      {/* ─── Right: Detail view ────────────────────────────────────────────── */}
      <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">

        {!selectedDocId ? (
          /* Empty state */
          <div className="flex h-full flex-col items-center justify-center gap-3 text-gray-400">
            <svg className="h-12 w-12 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <p className="text-sm">Select a document to explore</p>
          </div>
        ) : !docStatus ? (
          <div className="flex h-full items-center justify-center"><LoadingSpinner /></div>
        ) : (
          <>
            {/* ── Document header ── */}
            <div className="flex-shrink-0 border-b border-gray-200 bg-white px-6 py-4">
              {/* Title row */}
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate text-sm font-semibold text-gray-800">{docStatus.filename}</h3>
                    <StatusBadge status={docStatus.status} />
                    {docStatus.retry_count > 0 && (
                      <span className="text-xs text-amber-600">retries: {docStatus.retry_count}</span>
                    )}
                  </div>

                  {/* Metadata grid */}
                  <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
                    {[
                      docStatus.page_count      != null && ['Pages',      String(docStatus.page_count)],
                      ['Chunks',       String(docStatus.total_chunks)],
                      ['Embeddings',   String(docStatus.total_embeddings)],
                      docStatus.file_size_bytes != null && ['Size',        fmtBytes(docStatus.file_size_bytes)],
                      docStatus.language_detected && ['Language',   docStatus.language_detected],
                      docStatus.s3_uri && ['S3',          docStatus.s3_uri.split('/').pop() ?? ''],
                      docStatus.created_at && ['Created',     fmtDate(docStatus.created_at)],
                      docStatus.completed_at && ['Completed',   fmtDate(docStatus.completed_at)],
                    ].filter((x): x is [string, string] => Array.isArray(x)).map(([k, v]) => (
                      <div key={k as string} className="text-xs">
                        <dt className="inline font-medium text-gray-600">{k}: </dt>
                        <dd className="inline text-gray-500">{v}</dd>
                      </div>
                    ))}
                    {docStatus.has_text  && <span className="text-xs text-blue-500">has-text</span>}
                    {docStatus.has_images && <span className="text-xs text-violet-500">has-images</span>}
                  </dl>
                </div>

                <button
                  onClick={() =>
                    retrievalApi.getDownloadUrl(selectedDocId)
                      .then((r) => window.open(r.url, '_blank'))
                      .catch(() => {})
                  }
                  className="flex-shrink-0 rounded bg-gray-100 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-200"
                >
                  Download PDF
                </button>
              </div>

              {/* Error */}
              {docStatus.error_message && (
                <div className="mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                  Error: {docStatus.error_message}
                </div>
              )}

              {/* Pipeline diagram */}
              <div className="mt-4">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                  Ingestion Pipeline
                </p>
                <PipelineDiagram stages={docStatus.pipeline_stages} />
              </div>

              {/* Stage detail chips (model used + detail text) */}
              {docStatus.pipeline_stages.some((s) => s.model || s.detail) && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {docStatus.pipeline_stages
                    .filter((s) => s.model || s.detail)
                    .map((s) => (
                      <div key={s.name} className="rounded-md bg-gray-50 px-2.5 py-1 text-xs">
                        <span className="font-medium text-gray-600">{s.label}:</span>
                        {s.model  && <span className="ml-1 text-blue-600">{s.model}</span>}
                        {s.detail && <span className="ml-1 text-gray-400">({s.detail})</span>}
                      </div>
                    ))}
                </div>
              )}

              {/* Chunk status mini-bar (during / after processing) */}
              {docStatus.total_chunks > 0 && (
                <div className="mt-3 flex items-center gap-3 text-xs text-gray-500">
                  <span>Embedding progress:</span>
                  <div className="flex-1 max-w-xs h-1.5 rounded-full bg-gray-200 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-emerald-400 transition-all duration-500"
                      style={{ width: `${Math.round((docStatus.chunks_done / docStatus.total_chunks) * 100)}%` }}
                    />
                  </div>
                  <span>{docStatus.chunks_done}/{docStatus.total_chunks}</span>
                </div>
              )}
            </div>

            {/* ── Tabs ── */}
            <div className="flex flex-shrink-0 border-b border-gray-200 bg-white px-6">
              {(['chunks', 'process'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`-mb-px border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                    activeTab === tab
                      ? 'border-blue-500 text-blue-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700'
                  }`}
                >
                  {tab === 'chunks' ? `Chunks (${chunks.length})` : 'Process & Decisions'}
                </button>
              ))}
            </div>

            {/* ── Tab content (scrollable) ── */}
            <div className="flex-1 overflow-y-auto p-6">
              {activeTab === 'chunks' ? (
                /* ── Chunks tab ── */
                <>
                  {/* Chunk search + stats */}
                  <div className="mb-4 flex flex-wrap items-center gap-3">
                    <input
                      type="text"
                      value={chunkSearch}
                      onChange={(e) => setChunkSearch(e.target.value)}
                      placeholder="Filter chunk text…"
                      className="w-full max-w-sm rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
                    />
                    {chunkSearch && (
                      <span className="text-xs text-gray-400">
                        {filteredChunks.length} match{filteredChunks.length !== 1 ? 'es' : ''}
                      </span>
                    )}
                    {chunks.length > 0 && !chunkSearch && (
                      <button
                        onClick={() => {
                          if (expandedIds.size === chunks.length) {
                            setExpandedIds(new Set());
                          } else {
                            setExpandedIds(new Set(chunks.map((c) => c.chunk_id)));
                          }
                        }}
                        className="ml-auto text-xs text-blue-500 hover:text-blue-700"
                      >
                        {expandedIds.size === chunks.length ? 'Collapse all' : 'Expand all'}
                      </button>
                    )}
                  </div>

                  {chunksLoading ? (
                    <div className="flex justify-center py-12"><LoadingSpinner /></div>
                  ) : filteredChunks.length === 0 ? (
                    <p className="text-sm text-gray-400">{chunkSearch ? 'No matching chunks.' : 'No chunks found.'}</p>
                  ) : (
                    <div className="space-y-3">
                      {filteredChunks.map((chunk) => (
                        <ChunkDetailCard
                          key={chunk.chunk_id}
                          chunk={chunk}
                          totalChunks={chunks.length}
                          expanded={expandedIds.has(chunk.chunk_id)}
                          highlighted={chunk.chunk_id === focusedChunkId}
                          onToggle={() => toggleExpand(chunk.chunk_id)}
                        />
                      ))}
                    </div>
                  )}
                </>
              ) : (
                /* ── Process tab ── */
                chunksLoading ? (
                  <div className="flex justify-center py-12"><LoadingSpinner /></div>
                ) : (
                  <ChunkProcessView
                    chunks={chunks}
                    focusedChunkId={focusedChunkId}
                    onChunkFocus={handleChunkFocus}
                  />
                )
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
