import { useCallback, useRef, useState } from 'react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type NodeStatus =
  | 'queued'
  | 'running'
  | 'reviewing'
  | 'awaiting-input'
  | 'done'
  | 'rejected'
  | 'failed';

export interface ToolHit {
  name: string;
  args: Record<string, unknown>;
  at: number;
}

export interface TaskNode {
  taskId: string;
  index: number;
  role?: string;         // planner-chosen label (Researcher/Analyst/...), goal-specific
  description: string;
  dependsOn: string[];
  requiresUserInput: boolean;
  plannerModel: string; // "auto" or model id (from the plan graph)
  activeModel: string;  // effective model after pre-assign / review
  status: NodeStatus;
  attempts: number;
  tools: ToolHit[];
  output?: string;
  feedback?: string;
  question?: string;
  // Dependency outputs shown alongside the question when a task pauses for
  // input, so the operator sees the real options before approving.
  inputContext?: Record<string, string>;
  error?: string;
  retryReason?: string;
  promptTokens?: number;
  completionTokens?: number;
  startedAt?: number;
  endedAt?: number;
  // ids of dependency nodes that fed this node when it started (wire pulse)
  signalFrom: string[];
}

export type ConsolePhase =
  | 'idle'
  | 'planning'
  | 'awaiting-review'
  | 'running'
  | 'done'
  | 'failed';

export interface ReviewTask {
  task_id: string;
  task_index: number;
  role?: string;
  description: string;
  depends_on: string[];
  planner_model: string;
  requires_user_input: boolean;
}

export interface ConsoleState {
  phase: ConsolePhase;
  goal: string;
  runIdHint?: string;
  nodes: Record<string, TaskNode>;
  order: string[];
  reviewTasks: ReviewTask[] | null;
  modelForced: string | null;
  finalOutput: string | null;
  totalTokens: { prompt: number; completion: number };
  eventCount: number;
  startedAt?: number;
  finishedAt?: number;
}

const initial: ConsoleState = {
  phase: 'idle',
  goal: '',
  nodes: {},
  order: [],
  reviewTasks: null,
  modelForced: null,
  finalOutput: null,
  totalTokens: { prompt: 0, completion: 0 },
  eventCount: 0
};

interface Frame {
  type: string;
  data?: Record<string, any>;
}

const now = () => Date.now();

function withNode(
  s: ConsoleState,
  taskId: string,
  patch: (n: TaskNode) => Partial<TaskNode>
): ConsoleState {
  const node = s.nodes[taskId];
  if (!node) return s;
  return { ...s, nodes: { ...s.nodes, [taskId]: { ...node, ...patch(node) } } };
}

// Token frames patch the node AND add to the run total in one step.
function withNodeTokens(
  s: ConsoleState,
  taskId: string,
  d: Record<string, any>,
  patch: (n: TaskNode) => Partial<TaskNode>
): ConsoleState {
  const p = Number(d.prompt_tokens) || 0;
  const c = Number(d.completion_tokens) || 0;
  const next = withNode(s, taskId, n => ({
    promptTokens: (n.promptTokens ?? 0) + p,
    completionTokens: (n.completionTokens ?? 0) + c,
    ...patch(n),
  }));
  return p || c
    ? { ...next, totalTokens: { prompt: s.totalTokens.prompt + p, completion: s.totalTokens.completion + c } }
    : next;
}

// Pure reducer: one console state per WS frame. Kept side-effect free so the
// canvas only re-renders the nodes whose data actually changed.
export function reduceFrame(s: ConsoleState, f: Frame): ConsoleState {
  const d = (f.data || {}) as Record<string, any>;
  const base: ConsoleState = { ...s, eventCount: s.eventCount + 1 };

  switch (f.type) {
    case 'execution_started':
      return {
        ...initial,
        goal: String(d.goal ?? ''),
        runIdHint: String(d.run_id ?? '') || undefined,
        phase: 'planning',
        startedAt: now(),
        eventCount: base.eventCount
      };

    case 'planning_started':
      return { ...base, phase: 'planning' };

    case 'planning_failed':
      return { ...base, phase: 'failed' };

    case 'planning_completed': {
      const tasks: ReviewTask[] = d.tasks || [];
      const nodes: Record<string, TaskNode> = {};
      const order: string[] = [];
      for (const t of tasks) {
        order.push(t.task_id);
        nodes[t.task_id] = {
          taskId: t.task_id,
          index: t.task_index,
          role: t.role ? String(t.role) : undefined,
          description: t.description,
          dependsOn: t.depends_on || [],
          requiresUserInput: !!t.requires_user_input,
          plannerModel: t.planner_model || 'auto',
          activeModel: t.planner_model || 'auto',
          status: 'queued',
          attempts: 0,
          tools: [],
          signalFrom: []
        };
      }
      return { ...base, nodes, order, phase: 'running' };
    }

    case 'model_selection':
      return { ...base, modelForced: String(d.model ?? '') };

    case 'model_guardrail': {
      // Backend corrected picks outside the fetched catalogue; reflect the
      // fallback model on each affected node.
      let next = base;
      for (const c of (d.corrected || []) as { task_id: string; to: string }[]) {
        next = withNode(next, c.task_id, () => ({ activeModel: c.to }));
      }
      return next;
    }

    case 'task_model_retry':
      // Step failed on one model and is being repeated on the next; the
      // node keeps running but changes engine.
      return withNode(base, String(d.task_id ?? ''), () => ({
        activeModel: String(d.to_model ?? ''),
        retryReason: String(d.reason ?? 'model rotated'),
      }));

    case 'plan_models_applied': {
      let next = base;
      const assignments: Record<string, string> = d.assignments || {};
      for (const [tid, model] of Object.entries(assignments)) {
        next = withNode(next, tid, () => ({ activeModel: model }));
      }
      return next;
    }

    case 'plan_review_requested':
      return { ...base, phase: 'awaiting-review', reviewTasks: d.tasks || [] };

    case 'plan_review_completed': {
      let next: ConsoleState = { ...base, phase: 'running', reviewTasks: null };
      const assignments: Record<string, string> = d.assignments || {};
      for (const [tid, model] of Object.entries(assignments)) {
        next = withNode(next, tid, () => ({ activeModel: model }));
      }
      return next;
    }

    case 'plan_review_timeout':
      return { ...base, phase: 'running', reviewTasks: null };

    case 'task_started':
      return withNode(base, String(d.task_id ?? ''), n => ({
        status: 'running',
        startedAt: n.startedAt ?? now(),
        question: undefined,
        signalFrom: n.dependsOn.filter(dep => s.nodes[dep]?.status === 'done')
      }));

    case 'tool_execution':
      return withNode(base, String(d.task_id ?? ''), n => ({
        tools: [...n.tools, { name: String(d.tool_name), args: d.arguments || {}, at: now() }]
      }));

    case 'task_retry':
      return withNode(base, String(d.task_id ?? ''), n => ({
        status: 'running',
        attempts: Number(d.attempt ?? n.attempts + 1),
        retryReason: String(d.reason ?? '')
      }));

    case 'task_worker_completed':
      return withNodeTokens(base, String(d.task_id ?? ''), d, () => ({
        status: 'reviewing',
        output: String(d.output ?? ''),
        attempts: Number(d.attempts ?? 0) || 1
      }));

    case 'task_review_started':
      return withNode(base, String(d.task_id ?? ''), () => ({ status: 'reviewing' }));

    case 'task_review_passed':
      return withNodeTokens(base, String(d.task_id ?? ''), d, () => ({
        status: 'done',
        feedback: String(d.feedback ?? ''),
        endedAt: now(),
        signalFrom: []
      }));

    case 'task_review_failed':
      // Ambiguous: may be followed by a retry (back to running) or a final
      // rejection; keep the node in review until the decisive frame lands.
      return withNodeTokens(base, String(d.task_id ?? ''), d, n => ({
        status: n.status === 'running' ? 'running' : 'reviewing',
        feedback: String(d.feedback ?? d.error ?? '')
      }));

    case 'task_requires_input':
      return withNode(base, String(d.task_id ?? ''), n => ({
        status: 'awaiting-input',
        question: String(d.question ?? 'Input required'),
        inputContext: (d.context && typeof d.context === 'object')
          ? d.context as Record<string, string>
          : undefined,
        feedback: undefined
      }));

    case 'task_resumed':
      return withNode(base, String(d.task_id ?? ''), () => ({
        status: 'running',
        question: undefined
      }));

    case 'task_input_timeout':
      return withNode(base, String(d.task_id ?? ''), () => ({
        status: 'failed',
        error: String(d.reason ?? 'No user feedback'),
        endedAt: now()
      }));

    case 'task_worker_failed':
      return withNode(base, String(d.task_id ?? ''), n => ({
        status: 'failed',
        error: String(d.error ?? 'Worker failed'),
        attempts: n.attempts || 3,
        endedAt: now()
      }));

    case 'task_fallback_exhausted':
      return withNode(base, String(d.task_id ?? ''), () => ({
        status: 'failed',
        error: `model exhausted: ${d.primary_model}${d.fallback_model ? ` -> ${d.fallback_model}` : ''}`,
        endedAt: now()
      }));

    case 'task_retry_worker_failed':
      return withNode(base, String(d.task_id ?? ''), n => ({
        status: 'failed',
        error: String(d.error ?? 'Retry failed'),
        endedAt: now(),
        attempts: n.attempts + 1
      }));

    case 'task_retry_review_passed':
      return withNodeTokens(base, String(d.task_id ?? ''), d, n => ({
        status: 'done',
        feedback: String(d.feedback ?? ''),
        attempts: Math.max(n.attempts, 2),
        endedAt: now()
      }));

    case 'task_retry_review_failed':
      return withNodeTokens(base, String(d.task_id ?? ''), d, n => ({
        status: 'rejected',
        feedback: String(d.feedback ?? d.error ?? ''),
        attempts: Math.max(n.attempts, 2),
        endedAt: now()
      }));

    case 'run_completed': {
      // Backend total is authoritative; use it instead of the live tally.
      const tot = (d.summary || {}).total_tokens || {};
      return {
        ...base,
        finalOutput: String(d.final_output ?? ''),
        totalTokens: {
          prompt: Number(tot.prompt) || base.totalTokens.prompt,
          completion: Number(tot.completion) || base.totalTokens.completion
        },
        phase: String((d.summary || {}).status ?? '') === 'completed' ? 'done' : 'failed',
        finishedAt: now(),
        reviewTasks: null
      };
    }

    case 'execution_failed':
      return { ...base, phase: 'failed', finishedAt: now(), reviewTasks: null };

    default:
      return base;
  }
}

export interface EventLine {
  seq: number;
  at: number;
  type: string;
  data: Record<string, any>;
}

const LOG_CAP = 400;

export function useOrchestration() {
  const [state, setState] = useState<ConsoleState>(initial);
  const [log, setLog] = useState<EventLine[]>([]);
  const seqRef = useRef(0);

  const ingest = useCallback((frame: Frame) => {
    if (!frame || typeof frame.type !== 'string') return;
    setState(prev => reduceFrame(prev, frame));
    seqRef.current += 1;
    const line: EventLine = {
      seq: seqRef.current,
      at: now(),
      type: frame.type,
      data: frame.data || {}
    };
    setLog(prev => {
      const next = prev.length >= LOG_CAP ? prev.slice(prev.length - LOG_CAP + 1) : prev.slice();
      next.push(line);
      return next;
    });
  }, []);

  return { state, log, ingest };
}

// ---------------------------------------------------------------------------
// Topological layout for the canvas (levels = wave structure of the DAG)
// ---------------------------------------------------------------------------

export interface PositionedNode extends TaskNode {
  level: number;
  row: number;
}

export function layoutGraph(state: ConsoleState): PositionedNode[] {
  const levels: Record<string, number> = {};
  const resolve = (id: string, seen: Set<string>): number => {
    if (levels[id] !== undefined) return levels[id];
    if (seen.has(id)) return 0; // cycle guard
    seen.add(id);
    const node = state.nodes[id];
    const depLevels = node && node.dependsOn.length
      ? Math.max(...node.dependsOn.map(dep => (state.nodes[dep] ? resolve(dep, seen) : -1))) + 1
      : 0;
    levels[id] = depLevels;
    return depLevels;
  };
  for (const id of state.order) resolve(id, new Set());

  const byLevel: Record<number, string[]> = {};
  for (const id of state.order) {
    const lv = levels[id] ?? 0;
    (byLevel[lv] ||= []).push(id);
  }

  const out: PositionedNode[] = [];
  for (const id of state.order) {
    const lv = levels[id] ?? 0;
    out.push({
      ...state.nodes[id],
      level: lv,
      row: (byLevel[lv] || []).indexOf(id)
    });
  }
  return out;
}
