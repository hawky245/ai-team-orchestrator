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

  const color = d.dead ? 'hsl(342 100% 60%)' : d.flowing ? 'hsl(184 100% 55%)' : 'hsl(184 60% 50% / 0.4)';

  return (
    <>
      {(d.flowing || d.dead) && (
        <path
          id={`${id}-glow`}
          d={path}
          fill="none"
          stroke={d.dead ? 'hsl(342 100% 60% / 0.35)' : 'hsl(184 100% 55% / 0.35)'}
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
        strokeOpacity={d.flowing ? 1 : 0.8}
        className={d.flowing ? 'wire-flow' : 'wire-idle'}
        style={d.flowing || d.dead ? undefined : { filter: 'drop-shadow(0 0 2px hsl(184 100% 50% / 0.35))' }}
      />
    </>
  );
}
