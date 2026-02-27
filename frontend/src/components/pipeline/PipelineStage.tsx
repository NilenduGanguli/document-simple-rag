import type { PipelineStageInfo } from '../../types';

const statusFills: Record<string, string> = {
  completed: '#10b981',
  active: '#3b82f6',
  pending: '#e5e7eb',
  failed: '#ef4444',
};

const statusText: Record<string, string> = {
  completed: '#ffffff',
  active: '#ffffff',
  pending: '#6b7280',
  failed: '#ffffff',
};

interface Props {
  x: number;
  y: number;
  width: number;
  height: number;
  stage: PipelineStageInfo;
}

export default function PipelineStage({ x, y, width, height, stage }: Props) {
  const fill = statusFills[stage.status] || statusFills.pending;
  const textColor = statusText[stage.status] || statusText.pending;
  const isActive = stage.status === 'active';

  return (
    <g className={isActive ? 'stage-active' : ''}>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx={8}
        ry={8}
        fill={fill}
        style={{ transition: 'fill 0.4s ease' }}
      />
      {/* Stage label */}
      <text
        x={x + width / 2}
        y={y + height / 2 - (stage.detail ? 4 : 0)}
        textAnchor="middle"
        dominantBaseline="middle"
        fill={textColor}
        fontSize={11}
        fontWeight={600}
        style={{ transition: 'fill 0.4s ease' }}
      >
        {stage.label}
      </text>
      {/* Detail text */}
      {stage.detail && (
        <text
          x={x + width / 2}
          y={y + height / 2 + 12}
          textAnchor="middle"
          dominantBaseline="middle"
          fill={textColor}
          fontSize={8}
          opacity={0.85}
        >
          {stage.detail}
        </text>
      )}
      {/* Model badge */}
      {stage.model && stage.status !== 'pending' && (
        <text
          x={x + width / 2}
          y={y + height + 14}
          textAnchor="middle"
          dominantBaseline="middle"
          fill="#6b7280"
          fontSize={7}
        >
          {stage.model}
        </text>
      )}
      {/* Status icon */}
      {stage.status === 'completed' && (
        <g transform={`translate(${x + width - 14}, ${y + 4})`}>
          <circle cx="5" cy="5" r="5" fill="#065f46" opacity={0.3} />
          <path d="M3 5l2 2 4-4" stroke="#ffffff" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </g>
      )}
      {stage.status === 'failed' && (
        <g transform={`translate(${x + width - 14}, ${y + 4})`}>
          <circle cx="5" cy="5" r="5" fill="#7f1d1d" opacity={0.3} />
          <path d="M3 3l4 4M7 3l-4 4" stroke="#ffffff" strokeWidth="1.5" fill="none" strokeLinecap="round" />
        </g>
      )}
      {stage.status === 'active' && (
        <g transform={`translate(${x + width - 14}, ${y + 4})`}>
          <circle cx="5" cy="5" r="4" fill="none" stroke="#ffffff" strokeWidth="1.5" opacity={0.6}>
            <animate attributeName="r" values="3;5;3" dur="1.5s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.6;0.2;0.6" dur="1.5s" repeatCount="indefinite" />
          </circle>
        </g>
      )}
    </g>
  );
}
