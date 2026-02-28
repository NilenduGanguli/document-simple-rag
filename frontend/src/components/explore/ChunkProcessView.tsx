/**
 * ChunkProcessView — visual diagrams explaining how chunks were created.
 *
 * Panels:
 *  1. Chunking Summary — aggregate stats
 *  2. Document Map     — proportional ribbon, one block per chunk, colored by source_type,
 *                        page-boundary markers, click to jump to chunk
 *  3. Token Distribution — bar chart (one bar per chunk), 512 limit line, average line
 *  4. Splitting Decisions — categorised breakdown with explanations
 */
import type { ChunkItem } from '../../types';

const MAX_TOKENS = 512;

const SOURCE_PALETTE: Record<string, { fill: string; stroke: string; label: string }> = {
  text:  { fill: '#93c5fd', stroke: '#3b82f6', label: 'Text' },
  ocr:   { fill: '#fcd34d', stroke: '#f59e0b', label: 'OCR' },
  image: { fill: '#c4b5fd', stroke: '#8b5cf6', label: 'Image' },
  mixed: { fill: '#d1d5db', stroke: '#6b7280', label: 'Mixed' },
};
const DEFAULT_PALETTE = { fill: '#d1d5db', stroke: '#9ca3af', label: 'Other' };

interface Props {
  chunks: ChunkItem[];
  focusedChunkId: string | null;
  /** Called when the user clicks a chunk in a diagram — switches tab + expands that chunk */
  onChunkFocus: (id: string) => void;
}

export default function ChunkProcessView({ chunks, focusedChunkId, onChunkFocus }: Props) {
  if (chunks.length === 0) {
    return <p className="py-8 text-center text-sm text-gray-400">No chunks to display.</p>;
  }

  const tokenCounts = chunks.map((c) => c.token_count ?? 0);
  const totalTokens  = tokenCounts.reduce((a, b) => a + b, 0);
  const avgTokens    = Math.round(totalTokens / chunks.length);
  const minTokens    = Math.min(...tokenCounts);
  const maxChunkTok  = Math.max(...tokenCounts);

  const sourceBreakdown: Record<string, number> = {};
  for (const c of chunks) sourceBreakdown[c.source_type] = (sourceBreakdown[c.source_type] ?? 0) + 1;

  const pages        = chunks.filter((c) => c.page_number != null).map((c) => c.page_number as number);
  const pageMin      = pages.length ? Math.min(...pages) : null;
  const pageMax      = pages.length ? Math.max(...pages) : null;

  const maxSizeCount = chunks.filter((c) => (c.token_count ?? 0) >= 490).length;
  const shortCount   = chunks.filter((c) => (c.token_count ?? 0) < 80).length;
  const naturalCount = chunks.length - maxSizeCount - shortCount;

  return (
    <div className="space-y-6">

      {/* ── 1. Summary stats ───────────────────────────────────────────────── */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <h4 className="mb-4 text-sm font-semibold text-gray-700">Chunking Summary</h4>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Total chunks"  value={String(chunks.length)} />
          <StatTile label="Avg tokens"    value={String(avgTokens)} sub={`/ ${MAX_TOKENS} max`} />
          <StatTile label="Min tokens"    value={String(minTokens)} />
          <StatTile label="Max tokens"    value={String(maxChunkTok)} />
        </div>
        {pageMin != null && (
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="Pages covered" value={pageMin === pageMax ? String(pageMin) : `${pageMin} – ${pageMax}`} />
            <StatTile label="Max-size splits" value={`${maxSizeCount}`} sub={pct(maxSizeCount, chunks.length)} color="text-red-600" />
            <StatTile label="Natural splits"  value={`${naturalCount}`} sub={pct(naturalCount, chunks.length)} color="text-emerald-600" />
            <StatTile label="Short tails"     value={`${shortCount}`}   sub={pct(shortCount, chunks.length)}   color="text-amber-600" />
          </div>
        )}
        {/* Source legend */}
        <div className="mt-4 flex flex-wrap gap-4 border-t border-gray-100 pt-4">
          {Object.entries(sourceBreakdown).map(([type, count]) => {
            const pal = SOURCE_PALETTE[type] ?? DEFAULT_PALETTE;
            return (
              <div key={type} className="flex items-center gap-1.5 text-xs text-gray-600">
                <span className="h-3 w-5 rounded-sm" style={{ backgroundColor: pal.fill, border: `1px solid ${pal.stroke}` }} />
                <span>{pal.label}</span>
                <span className="font-semibold">{count}</span>
                <span className="text-gray-400">({pct(count, chunks.length)})</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── 2. Document map (ribbon) ───────────────────────────────────────── */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <h4 className="mb-1 text-sm font-semibold text-gray-700">Document Map</h4>
        <p className="mb-4 text-xs text-gray-400">
          Each block = one chunk, width proportional to token count. Page markers shown above.
          Click a block to inspect that chunk.
        </p>
        <ChunkRibbon chunks={chunks} focusedChunkId={focusedChunkId} onChunkFocus={onChunkFocus} />
      </div>

      {/* ── 3. Token distribution bar chart ───────────────────────────────── */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <h4 className="mb-1 text-sm font-semibold text-gray-700">Token Distribution</h4>
        <p className="mb-4 text-xs text-gray-400">
          Bar height = token count per chunk. <span className="text-red-500">Red dashed</span> = {MAX_TOKENS}-token limit.{' '}
          <span className="text-green-600">Green dashed</span> = average ({avgTokens}).
        </p>
        <TokenDistChart chunks={chunks} avgTokens={avgTokens} focusedChunkId={focusedChunkId} />
      </div>

      {/* ── 4. Splitting decisions breakdown ──────────────────────────────── */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <h4 className="mb-3 text-sm font-semibold text-gray-700">Splitting Decisions</h4>
        <p className="mb-4 text-xs text-gray-400">
          The ingestion worker uses a <strong>RecursiveCharacterSplitter</strong> that tries separators
          in order: paragraph breaks → sentence ends → words → characters. The decisions below
          are inferred from each chunk's token count and source type.
        </p>
        <div className="space-y-3">
          <DecisionRow
            fill="#fca5a5" border="#ef4444"
            label="Max-size splits"
            count={maxSizeCount} total={chunks.length}
            description={
              `${maxSizeCount} chunk${maxSizeCount !== 1 ? 's' : ''} reached ≥ 490 tokens. ` +
              'The splitter exhausted coarser separators and had to fall back to finer ones ' +
              '(sentence- or word-level). Consider reducing the target chunk size if this is high.'
            }
          />
          <DecisionRow
            fill="#bbf7d0" border="#10b981"
            label="Natural boundary splits"
            count={naturalCount} total={chunks.length}
            description={
              `${naturalCount} chunk${naturalCount !== 1 ? 's' : ''} ended at a paragraph or sentence boundary ` +
              'well within the token limit — ideal splits that preserve semantic coherence.'
            }
          />
          <DecisionRow
            fill="#fde68a" border="#f59e0b"
            label="Short tail chunks"
            count={shortCount} total={chunks.length}
            description={
              `${shortCount} chunk${shortCount !== 1 ? 's' : ''} contain fewer than 80 tokens. ` +
              'Likely the last fragment of a section, a standalone heading, or a caption. ' +
              'These may have lower embedding quality due to limited context.'
            }
          />
          {(sourceBreakdown['ocr'] ?? 0) > 0 && (
            <DecisionRow
              fill="#ddd6fe" border="#8b5cf6"
              label="OCR-sourced chunks"
              count={sourceBreakdown['ocr']} total={chunks.length}
              description={
                `${sourceBreakdown['ocr']} chunk${sourceBreakdown['ocr'] !== 1 ? 's' : ''} were extracted ` +
                'from scanned pages via the OCR service (Tesseract/image pipeline) rather than ' +
                'direct PDF text extraction. OCR output may have more noise/typos.'
              }
            />
          )}
        </div>
      </div>

    </div>
  );
}

// ── ChunkRibbon ─────────────────────────────────────────────────────────────

function ChunkRibbon({
  chunks,
  focusedChunkId,
  onChunkFocus,
}: {
  chunks: ChunkItem[];
  focusedChunkId: string | null;
  onChunkFocus: (id: string) => void;
}) {
  const totalTokens = chunks.reduce((a, c) => a + (c.token_count ?? 1), 0);
  const W = 1000;
  const RIBBON_H = 36;
  const PAGE_LABEL_Y = RIBBON_H + 16;
  const SVG_H = PAGE_LABEL_Y + 12;

  // Compute cumulative x positions
  let cursor = 0;
  const segments = chunks.map((c) => {
    const w = Math.max(2, ((c.token_count ?? 1) / totalTokens) * W);
    const x = cursor;
    cursor += w;
    return { chunk: c, x, w };
  });

  // Find page transition positions
  const pageMarkers: { x: number; page: number }[] = [];
  let lastPage: number | null = null;
  for (const seg of segments) {
    const p = seg.chunk.page_number;
    if (p != null && p !== lastPage) {
      pageMarkers.push({ x: seg.x, page: p });
      lastPage = p;
    }
  }

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${W} ${SVG_H}`}
        className="w-full"
        style={{ minWidth: '400px', maxHeight: '80px' }}
        preserveAspectRatio="none"
      >
        {segments.map(({ chunk, x, w }) => {
          const pal    = SOURCE_PALETTE[chunk.source_type] ?? DEFAULT_PALETTE;
          const isFocused = chunk.chunk_id === focusedChunkId;
          return (
            <g key={chunk.chunk_id}>
              <rect
                x={x + 0.5}
                y={0}
                width={Math.max(1, w - 1)}
                height={RIBBON_H}
                fill={pal.fill}
                stroke={isFocused ? '#1d4ed8' : pal.stroke}
                strokeWidth={isFocused ? 2 : 0.5}
                rx={1}
                style={{ cursor: 'pointer' }}
                onClick={() => onChunkFocus(chunk.chunk_id)}
              >
                <title>#{chunk.chunk_index} | {chunk.token_count ?? '?'} tokens{chunk.page_number != null ? ` | pg.${chunk.page_number}` : ''} | {chunk.source_type}</title>
              </rect>
            </g>
          );
        })}
        {/* Page boundary markers */}
        {pageMarkers.map(({ x, page }) => (
          <g key={`pg-${page}`}>
            <line x1={x} y1={0} x2={x} y2={RIBBON_H} stroke="#374151" strokeWidth={0.8} opacity={0.4} strokeDasharray="2,2" />
            <text x={x + 2} y={PAGE_LABEL_Y} fontSize={7} fill="#6b7280">p{page}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

// ── TokenDistChart ───────────────────────────────────────────────────────────

function TokenDistChart({
  chunks,
  avgTokens,
  focusedChunkId,
}: {
  chunks: ChunkItem[];
  avgTokens: number;
  focusedChunkId: string | null;
}) {
  const BAR_W  = 7;
  const GAP    = 2;
  const CHART_H = 100;
  const LABEL_H = 12;
  const SVG_H   = CHART_H + LABEL_H + 4;
  const totalW  = chunks.length * (BAR_W + GAP);

  const avgY   = CHART_H - (avgTokens / MAX_TOKENS) * CHART_H;
  const limitY = CHART_H - (MAX_TOKENS / MAX_TOKENS) * CHART_H; // = 0

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${totalW} ${SVG_H}`}
        className="w-full"
        style={{ minWidth: Math.min(totalW, 300) + 'px', height: '130px' }}
        preserveAspectRatio="xMinYMid meet"
      >
        {/* Limit line (512) */}
        <line x1={0} y1={limitY + 1} x2={totalW} y2={limitY + 1}
          stroke="#ef4444" strokeWidth={0.8} strokeDasharray="4,3" opacity={0.7} />
        {/* Avg line */}
        <line x1={0} y1={avgY} x2={totalW} y2={avgY}
          stroke="#10b981" strokeWidth={0.8} strokeDasharray="4,3" opacity={0.7} />

        {chunks.map((chunk, i) => {
          const tok  = chunk.token_count ?? 0;
          const barH = Math.max(1, (tok / MAX_TOKENS) * CHART_H);
          const x    = i * (BAR_W + GAP);
          const y    = CHART_H - barH;
          const pal  = SOURCE_PALETTE[chunk.source_type] ?? DEFAULT_PALETTE;
          const isFocused = chunk.chunk_id === focusedChunkId;
          const fill = tok >= 490 ? '#fca5a5' : tok < 80 ? '#fde68a' : pal.fill;
          const stroke = isFocused ? '#1d4ed8' : pal.stroke;
          return (
            <g key={chunk.chunk_id}>
              <rect
                x={x}
                y={y}
                width={BAR_W}
                height={barH}
                fill={fill}
                stroke={stroke}
                strokeWidth={isFocused ? 1.5 : 0.3}
                rx={1}
              >
                <title>#{chunk.chunk_index}: {tok} tokens</title>
              </rect>
            </g>
          );
        })}

        {/* X-axis baseline */}
        <line x1={0} y1={CHART_H} x2={totalW} y2={CHART_H} stroke="#d1d5db" strokeWidth={0.5} />
      </svg>
    </div>
  );
}

// ── DecisionRow ──────────────────────────────────────────────────────────────

function DecisionRow({
  fill, border, label, count, total, description,
}: {
  fill: string; border: string;
  label: string; count: number; total: number; description: string;
}) {
  const pctVal = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div className="flex gap-3">
      <div
        className="mt-0.5 h-4 w-4 flex-shrink-0 rounded-sm"
        style={{ backgroundColor: fill, border: `1px solid ${border}` }}
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-semibold text-gray-700">{label}</span>
          <span className="text-xs font-medium" style={{ color: border }}>{count}</span>
          <span className="text-xs text-gray-400">({pctVal}%)</span>
        </div>
        {/* Progress bar */}
        <div className="mt-1 h-1.5 w-full rounded-full bg-gray-100">
          <div
            className="h-1.5 rounded-full transition-all"
            style={{ width: `${pctVal}%`, backgroundColor: border, opacity: 0.5 }}
          />
        </div>
        <p className="mt-1 text-xs leading-relaxed text-gray-500">{description}</p>
      </div>
    </div>
  );
}

// ── StatTile / helpers ───────────────────────────────────────────────────────

function StatTile({ label, value, sub, color = 'text-gray-800' }: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div className="rounded-md bg-gray-50 px-3 py-2">
      <p className="text-xs text-gray-400">{label}</p>
      <p className={`mt-0.5 text-lg font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400">{sub}</p>}
    </div>
  );
}

function pct(n: number, total: number): string {
  return total > 0 ? `${Math.round((n / total) * 100)}%` : '0%';
}
