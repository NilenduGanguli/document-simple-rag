interface Props {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  status: 'completed' | 'active' | 'pending';
}

export default function PipelineConnection({ x1, y1, x2, y2, status }: Props) {
  const className =
    status === 'active'
      ? 'connection-active'
      : status === 'completed'
        ? 'connection-completed'
        : 'connection-pending';

  return (
    <g>
      <line
        x1={x1}
        y1={y1}
        x2={x2}
        y2={y2}
        className={className}
        strokeWidth={2}
        fill="none"
      />
      {/* Arrow head */}
      <polygon
        points={`${x2 - 6},${y2 - 3} ${x2},${y2} ${x2 - 6},${y2 + 3}`}
        fill={status === 'completed' ? '#10b981' : status === 'active' ? '#3b82f6' : '#d1d5db'}
        style={{ transition: 'fill 0.4s ease' }}
      />
    </g>
  );
}
