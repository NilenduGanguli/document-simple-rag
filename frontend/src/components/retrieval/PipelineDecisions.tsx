import { Fragment } from 'react';

interface Props {
  latencyBreakdown: Record<string, number>;
  entitiesDetected: string[];
}

const stageOrder = [
  { key: 'ner_ms', label: 'NER', model: 'BERT NER' },
  { key: 'embedding_ms', label: 'Embedding', model: 'BERT INT8 ONNX' },
  { key: 'dense_search_ms', label: 'Dense Search', model: 'PGVector HNSW' },
  { key: 'sparse_search_ms', label: 'Sparse Search', model: 'BM25Okapi' },
  { key: 'rrf_ms', label: 'RRF Fusion', model: null },
  { key: 'mmr_ms', label: 'MMR', model: null },
  { key: 'rerank_ms', label: 'Reranker', model: 'BERT Cross-encoder' },
];

export default function PipelineDecisions({ latencyBreakdown, entitiesDetected }: Props) {
  const totalMs = latencyBreakdown.total_ms || 0;
  const stageWidth = 100;
  const stageHeight = 44;
  const gap = 20;
  const padding = 12;
  const numStages = stageOrder.length;
  const totalWidth = numStages * stageWidth + (numStages - 1) * gap + padding * 2;
  const totalSvgHeight = stageHeight + 36 + padding * 2;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
          Retrieval Pipeline Decisions
        </h4>
        <span className="text-xs font-medium text-gray-800">
          Total: {totalMs.toFixed(0)}ms
        </span>
      </div>

      <svg viewBox={`0 0 ${totalWidth} ${totalSvgHeight}`} className="w-full" style={{ maxHeight: 110 }}>
        {stageOrder.map((stage, i) => {
          const ms = latencyBreakdown[stage.key] ?? 0;
          const x = padding + i * (stageWidth + gap);
          const y = padding;
          const centerY = y + stageHeight / 2;
          const isZero = ms === 0 && stage.key === 'rerank_ms';
          const isSlow = ms > 100;

          const fill = isZero ? '#9ca3af' : isSlow ? '#f59e0b' : '#3b82f6';
          const textColor = '#ffffff';

          return (
            <Fragment key={stage.key}>
              {i > 0 && (
                <g>
                  <line
                    x1={padding + (i - 1) * (stageWidth + gap) + stageWidth}
                    y1={centerY}
                    x2={x}
                    y2={centerY}
                    stroke="#10b981"
                    strokeWidth={2}
                  />
                  <polygon
                    points={`${x - 5},${centerY - 3} ${x},${centerY} ${x - 5},${centerY + 3}`}
                    fill="#10b981"
                  />
                </g>
              )}
              <g className="reveal-animate" style={{ animationDelay: `${i * 0.1}s` }}>
                <rect x={x} y={y} width={stageWidth} height={stageHeight} rx={6} ry={6} fill={fill} />
                <text
                  x={x + stageWidth / 2}
                  y={y + stageHeight / 2 - 5}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fill={textColor}
                  fontSize={10}
                  fontWeight={600}
                >
                  {stage.label}
                </text>
                <text
                  x={x + stageWidth / 2}
                  y={y + stageHeight / 2 + 8}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fill={textColor}
                  fontSize={9}
                  opacity={0.85}
                >
                  {isZero ? 'skipped' : `${ms.toFixed(0)}ms`}
                </text>
                {stage.model && (
                  <text
                    x={x + stageWidth / 2}
                    y={y + stageHeight + 12}
                    textAnchor="middle"
                    fill="#6b7280"
                    fontSize={7}
                  >
                    {stage.model}
                  </text>
                )}
              </g>
            </Fragment>
          );
        })}
      </svg>

      {entitiesDetected.length > 0 && (
        <div className="mt-3 flex items-center gap-2">
          <span className="text-xs font-medium text-gray-500">Entities:</span>
          {entitiesDetected.map((e, i) => (
            <span key={i} className="rounded-full bg-indigo-100 px-2 py-0.5 text-xs text-indigo-700">
              {e}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
