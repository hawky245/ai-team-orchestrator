import { getBezierPath, type EdgeProps } from '@xyflow/react';

interface SignalData {
  flowing?: boolean; // parent completed, child started: energy pulse along wire
  dead?: boolean;    // child failed
}

export function SignalEdge(props: EdgeProps) {
  const {
    id, sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition, data
  } = props;
  const d = (data || {}) as unknown as SignalData;
  const [path] = getBezierPath({
    sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition
  });

  const color = d.dead ? 'hsl(342 100% 60%)' : d.flowing ? 'hsl(184 100% 55%)' : 'hsl(215 18% 42%)';

  return (
    <>
      {d.flowing && (
        <path
          id={`${id}-glow`}
          d={path}
          fill="none"
          stroke="hsl(184 100% 55% / 0.35)"
          strokeWidth={5}
          style={{ filter: 'blur(3px)' }}
        />
      )}
      <path
        id={id}
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={d.flowing ? 1.8 : 1.2}
        strokeOpacity={d.flowing ? 1 : 0.65}
        className={d.flowing ? 'wire-flow' : 'wire-idle'}
      />
    </>
  );
}
