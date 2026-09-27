import { memo, useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import {
  ReactFlow, Background, BackgroundVariant, MarkerType,
  type Node, type Edge, type NodeChange, type ReactFlowInstance, type Viewport
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import TextType from '@/components/reactbits/TextType';
import { SignalEdge } from '@/console/SignalEdge';
import {
  layoutGraph,
  type ConsoleState,
  type PositionedNode
} from '@/console/useOrchestration';
import { FlowTaskNode, SlotCard, type TaskCanvasNode } from '@/console/TaskCanvasNode';

const SLOT_IDS = ['t1', 't2', 't3', 't4', 't5', 't6', 't7'];

const nodeTypes = { task: FlowTaskNode };
const edgeTypes = { signal: SignalEdge };

export interface PipelineCanvasProps {
  state: ConsoleState;
  models: string[];
  slotModels: Record<string, string>;
  assignmentOverride?: Record<string, string>;
  onAssignTask: (taskId: string, model: string) => void;
  onAssignSlot: (slot: string, model: string) => void;
  onClearSlots: () => void;
  onFeedback: (taskId: string, text: string) => void;
  onInspect: (taskId: string) => void;
  onPaneClick: () => void;
  selectedTaskId: string | null;
}

function buildTaskNode(
  p: PositionedNode,
  reviewMode: boolean,
  models: string[],
  props: PipelineCanvasProps,
  draggedPos?: { x: number; y: number }
): Node {
  const display = reviewMode && props.assignmentOverride?.[p.taskId]
    ? { ...p, activeModel: props.assignmentOverride[p.taskId] }
    : p;
  const data: TaskCanvasNode = {
    kind: 'task',
    node: display,
    reviewMode,
    models,
    onAssign: props.onAssignTask,
    onFeedback: props.onFeedback,
    onInspect: props.onInspect
  };
  return {
    id: p.taskId,
    type: 'task',
    position: draggedPos ?? { x: 40 + p.level * 310, y: 40 + p.row * 200 },
    data: data as unknown as Record<string, unknown>,
    selected: props.selectedTaskId === p.taskId
  };
}

function PipelineCanvasImpl(props: PipelineCanvasProps) {
  const { state, models } = props;
  const reviewMode = state.phase === 'awaiting-review';
  // The pre-flight board must survive a FAULT that happens before any task
  // exists (e.g. the planner call itself failed): otherwise the canvas goes
  // blank and the user loses the slots they just set up.
  const boardVisible =
    state.phase === 'idle' ||
    (state.phase === 'failed' && state.order.length === 0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const rfRef = useRef<ReactFlowInstance<Node> | null>(null);

  // User-dragged node positions survive the constant re-renders the event
  // stream causes: layout recomputes reuse these instead of the grid spots.
  const posRef = useRef<Record<string, { x: number; y: number }>>({});
  const [dragTick, bumpDrag] = useReducer(x => x + 1, 0);
  const prevPhaseRef = useRef(state.phase);
  useEffect(() => {
    // A fresh run (anything re-entering 'planning') starts a clean layout.
    if (state.phase === 'planning' && prevPhaseRef.current !== 'planning') {
      posRef.current = {};
    }
    // Returning to the board (idle/failed) must not inherit the last run's
    // pan/zoom, or the slots appear off-screen.
    if (state.phase === 'idle') setVp({ x: 0, y: 0, zoom: 1 });
    prevPhaseRef.current = state.phase;
  }, [state.phase]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    let moved = false;
    for (const ch of changes) {
      if (ch.type === 'position' && ch.position) {
        // Every change (including during-drag ones) is applied so the node
        // follows the pointer; the final entry leaves the persisted position.
        posRef.current[ch.id] = { x: ch.position.x, y: ch.position.y };
        moved = true;
      }
    }
    if (moved) bumpDrag();
  }, []);

  // The pre-flight slot cards live in a DOM overlay (React Flow swallows
  // their HTML5 drops), so nothing about them moved when the canvas
  // panned/zoomed — the board felt frozen. Mirror the flow viewport onto
  // the overlay so slots ride the same transform as the dot grid.
  const [vp, setVp] = useState<Viewport>({ x: 0, y: 0, zoom: 1 });

  // fitView runs once when the container may still be zero-sized (hidden tab,
  // flex layout settling); re-fit whenever the canvas actually resizes so
  // slots/nodes are never left cut off.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      if (el.clientWidth > 0 && el.clientHeight > 0) {
        rfRef.current?.fitView({ padding: 0.22, maxZoom: 1, duration: 150 });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { nodes, edges } = useMemo(() => {
    if (boardVisible) {
      // Pre-flight slots are plain DOM cards in a wrapping grid below —
      // reliable HTML5 drop targets, no canvas transform in the way.
      return { nodes: [] as Node[], edges: [] as Edge[] };
    }

    const laid = layoutGraph(state);
    const taskNodes = laid.map(p =>
      buildTaskNode(p, reviewMode, models, props, posRef.current[p.taskId])
    );
    const taskEdges: Edge[] = [];
    for (const p of laid) {
      for (const dep of p.dependsOn) {
        if (!state.nodes[dep]) continue;
        const depDone = p.signalFrom.includes(dep) || state.nodes[dep].status === 'done';
        taskEdges.push({
          id: `${dep}->${p.taskId}`,
          source: dep,
          target: p.taskId,
          type: 'signal',
          data: {
            flowing: depDone && (p.status === 'running' || p.status === 'reviewing' || p.status === 'awaiting-input'),
            dead: p.status === 'failed'
          },
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: 'hsl(215 18% 45%)' }
        });
      }
    }
    return { nodes: taskNodes, edges: taskEdges };
    // dragTick: user drags re-materialise the nodes array with new positions.
  }, [state, models, reviewMode, props.slotModels, props.selectedTaskId, props.assignmentOverride, dragTick]);

  // Re-fit whenever the graph structure changes (plan spawns, slots appear).
  // Delay past the commit so ReactFlow has measured the new node bounds, and
  // never fit against a zero-sized container — that bakes in a degenerate
  // transform which leaves the nodes invisible until the next resize.
  useEffect(() => {
    const fit = () => {
      const el = wrapRef.current;
      if (!el || el.clientWidth === 0 || el.clientHeight === 0) return;
      rfRef.current?.fitView({ padding: 0.22, maxZoom: 1 });
    };
    const t1 = window.setTimeout(fit, 60);
    const t2 = window.setTimeout(fit, 260);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [nodes.length, state.phase]);

  return (
    <div
      ref={wrapRef}
      className="relative h-full w-full"
      // Pane-level DnD safety net: React Flow can swallow node-level drops,
      // so hit-test the node under the cursor and route the assignment here.
      onDragOver={e => {
        if (boardVisible || reviewMode) e.preventDefault();
      }}
      onDrop={e => {
        if (state.phase !== 'idle' && !reviewMode) return;
        e.preventDefault();
        const model = e.dataTransfer.getData('text/plain');
        if (!model) return;
        const el = document.elementFromPoint(e.clientX, e.clientY);
        const nodeEl = el?.closest('.react-flow__node');
        const targetId = nodeEl?.getAttribute('data-id');
        if (!targetId) return;
        if (boardVisible) props.onAssignSlot(targetId, model);
        else props.onAssignTask(targetId, model);
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onPaneClick={props.onPaneClick}
        onNodesChange={onNodesChange}
        onMove={(_, v) => setVp(v)}
        onInit={inst => { rfRef.current = inst as ReactFlowInstance<Node>; }}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable={false}
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
        minZoom={0.3}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={26} size={1} color="rgba(0,240,255,0.09)" />
      </ReactFlow>

      {boardVisible && (
        <>
          <div className="pointer-events-none absolute left-4 top-4 z-10">
            <p className="hud-label">Pre-flight pipeline slots</p>
            <p className="mt-1 max-w-[380px] text-[11px] text-muted-foreground">
              Drag a model chip from the bay onto a task slot to pre-assign it.
              Slots the plan doesn&apos;t reach are ignored.
            </p>
          </div>
          {Object.values(props.slotModels).some(m => m && m !== 'auto') && (
            <button
              onClick={props.onClearSlots}
              className="absolute right-4 top-4 z-10 rounded-md border border-cyan-400/40 bg-cyan-400/10 px-3 py-1.5 text-[11px] font-bold tracking-wide text-cyan-100 hover:bg-cyan-400/20"
            >
              CLEAR ALL
            </button>
          )}
          {/* Pass-through except on the cards themselves, so wheel-zoom and
              pane-pan keep working on the canvas underneath. The transform
              keeps the board locked to the flow viewport (pan/zoom ride). */}
          <div
            className="pointer-events-none absolute inset-0 flex flex-wrap content-center items-center justify-center gap-4 p-6 pt-20"
            style={{
              transform: `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})`,
              transformOrigin: '0 0',
              willChange: 'transform'
            }}
          >
            {SLOT_IDS.map(slot => (
              <SlotCard
                key={slot}
                slotId={slot}
                model={props.slotModels[slot] ?? null}
                onAssign={props.onAssignSlot}
              />
            ))}
          </div>
        </>
      )}

      {state.phase === 'planning' && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <TextType
            text={['DECOMPISING OBJECTIVE…', 'BUILDING TASK DAG…']}
            typingSpeed={45}
            deletingSpeed={20}
            pauseDuration={1400}
            className="neon-text font-mono text-sm tracking-[0.3em]"
            cursorCharacter="▍"
          />
        </div>
      )}

      {reviewMode && (
        <div className="pointer-events-none absolute inset-x-0 bottom-4 flex justify-center">
          <div className="pointer-events-auto rounded-full border border-amber-400/40 bg-card/90 px-4 py-2 text-[12px] text-amber-200 backdrop-blur glow-amber">
            REVIEW MODE — assign a model per node, then press{' '}
            <span className="font-semibold">Start Execution</span> in the command bar
          </div>
        </div>
      )}

      {/* Pan/zoom persists across runs, so nodes can drift off-view mid-run
          (the "panels disappeared" symptom). One click brings them back. */}
      {!boardVisible && nodes.length > 0 && (
        <button
          onClick={() => rfRef.current?.fitView({ padding: 0.22, maxZoom: 1, duration: 200 })}
          className="absolute bottom-16 right-3 z-10 rounded-md border border-cyan-400/40 bg-cyan-400/10 px-3 py-1.5 text-[11px] font-bold tracking-wide text-cyan-100 hover:bg-cyan-400/20"
        >
          ⌖ RECENTER
        </button>
      )}
    </div>
  );
}

export const PipelineCanvas = memo(PipelineCanvasImpl);
