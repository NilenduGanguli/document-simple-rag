import { Fragment } from 'react';
import type { PipelineStageInfo } from '../../types';
import PipelineStage from './PipelineStage';
import PipelineConnection from './PipelineConnection';

interface Props {
  stages: PipelineStageInfo[];
}

export default function PipelineDiagram({ stages }: Props) {
  const stageWidth = 110;
  const stageHeight = 52;
  const gap = 32;
  const padding = 16;
  const totalWidth = stages.length * stageWidth + (stages.length - 1) * gap + padding * 2;
  const totalHeight = stageHeight + 40 + padding * 2; // extra space for model labels

  return (
    <svg
      viewBox={`0 0 ${totalWidth} ${totalHeight}`}
      className="w-full"
      style={{ maxHeight: 140 }}
    >
      {stages.map((stage, i) => {
        const x = padding + i * (stageWidth + gap);
        const y = padding;
        const centerY = y + stageHeight / 2;

        // Determine connection status
        let connStatus: 'completed' | 'active' | 'pending' = 'pending';
        if (stage.status === 'completed' || stage.status === 'active') {
          connStatus = stage.status === 'active' ? 'active' : 'completed';
        }

        return (
          <Fragment key={stage.name}>
            {i > 0 && (
              <PipelineConnection
                x1={padding + (i - 1) * (stageWidth + gap) + stageWidth}
                y1={centerY}
                x2={x}
                y2={centerY}
                status={connStatus}
              />
            )}
            <PipelineStage
              x={x}
              y={y}
              width={stageWidth}
              height={stageHeight}
              stage={stage}
            />
          </Fragment>
        );
      })}
    </svg>
  );
}
