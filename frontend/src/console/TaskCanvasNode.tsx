import { memo, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { CheckCircle2, XCircle, Loader2, Wrench, Cpu } from 'lucide-react';
import DecryptedText from '@/components/reactbits/DecryptedText';
import BorderGlow from '@/components/reactbits/BorderGlow';
import PixelCard from '@/components/reactbits/PixelCard';
import type { PositionedNode } from '@/console/useOrchestration';

export type CanvasNodeKind = 'task' | 'slot';

export interface TaskCanvasNode {
  kind: CanvasNodeKind;
  node?: PositionedNode;
  slotId?: string;          // for pre-flight slot nodes: "t1".."t7"
  slotModel?: string;       // pre-assigned model for that slot
  reviewMode?: boolean;     // plan-review gate open: model select active
  models?: string[];
  onAssign?: (taskId: string, model: string) => void;
  onFeedback?: (taskId: string, text: string) => void;
  onInspect?: (taskId: string) => void;
}

const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  queued: { label: 'QUEUED', cls: 'text-muted-foreground border-border' },
  running: { label: 'RUNNING', cls: 'text-cyan-300 border-cyan-400/40' },
  reviewing: { label: 'REVIEW', cls: 'text-sky-300 border-sky-400/40' },
  'awaiting-input': { label: 'AWAITING INPUT', cls: 'text-amber-300 border-amber-400/50' },
  done: { label: 'PASSED', cls: 'text-emerald-300 border-emerald-400/50' },
  rejected: { label: 'REJECTED', cls: 'text-rose-300 border-rose-400/40' },
  failed: { label: 'FAILED', cls: 'text-rose-300 border-rose-500/60' }
};

function ModelSelect({
  value, models, onChange
}: { value: string; models: string[]; onChange: (v: string) => void }) {
  return (
    <select
      className="nodrag h-7 w-full rounded border border-input bg-secondary/70 px-1.5 text-[11px] text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      value={value}
      onChange={e => onChange(e.target.value)}
    >
      <option value="auto">Auto ({'Planner decides'})</option>
      {models.map(m => <option key={m} value={m}>{m}</option>)}
    </select>
  );
}

function AwaitingInputBlock({
  question, inputContext, onSend
}: {
  question: string;
  inputContext?: Record<string, string>;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState('');
  const ctxEntries = Object.entries(inputContext ?? {});
  return (
    <div className="nodrag mt-2 space-y-1.5 rounded-md border border-amber-400/30 bg-amber-400/5 p-2">
      <p className="text-[11px] leading-snug text-amber-200/90">{question}</p>
      {ctxEntries.length > 0 && (
        <div className="max-h-40 space-y-1 overflow-y-auto rounded border border-amber-400/20 bg-background/60 p-1.5">
          <p className="hud-label text-[8px] text-amber-300/80">UPSTREAM RESULTS — READ BEFORE CHOOSING</p>
          {ctxEntries.map(([depId, out]) => (
            <div key={depId}>
              <span className="font-mono text-[9px] text-cyan-300">{depId}</span>
              <pre className="whitespace-pre-wrap break-words font-mono text-[10px] leading-snug text-amber-100/90">
                {out}
              </pre>
            </div>
          ))}
        </div>
      )}
      <input
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="Direction for the agent…"
        className="h-7 w-full rounded border border-amber-400/30 bg-background/70 px-2 text-[11px] text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-amber-400/50"
      />
      <div className="flex gap-1.5">
        <button
          onClick={() => onSend('APPROVED')}
          className="flex-1 rounded bg-amber-400/90 px-2 py-1 text-[11px] font-semibold text-black hover:bg-amber-300"
        >
          Approve
        </button>
        <button
          disabled={!text.trim()}
          onClick={() => onSend(text.trim())}
          className="flex-1 rounded border border-amber-400/50 px-2 py-1 text-[11px] font-semibold text-amber-200 hover:bg-amber-400/10 disabled:opacity-40"
        >
          Send Feedback
        </button>
      </div>
    </div>
  );
}

function TaskBody({ data }: { data: TaskCanvasNode }) {
  const node = data.node!;
  const badge = STATUS_BADGE[node.status];
  const hasModel = node.activeModel && node.activeModel !== 'auto';

  const inner = (
    <div
      onClick={() => data.onInspect?.(node.taskId)}
      onDragOver={data.reviewMode ? e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } : undefined}
      onDrop={data.reviewMode ? e => {
        e.preventDefault();
        e.stopPropagation();
        const m = e.dataTransfer.getData('text/plain');
        if (m) data.onAssign?.(node.taskId, m);
      } : undefined}
      // Review mode keeps nodes pinned (nodrag) so bay chip drops stay
      // rock-solid; outside review the whole card drags to reposition.
      className={`${data.reviewMode ? 'nodrag cursor-pointer' : 'cursor-grab active:cursor-grabbing'} w-[250px] rounded-xl border bg-card/95 p-3 backdrop-blur-sm transition-colors ${
        node.status === 'running' ? 'border-cyan-400/40' :
        node.status === 'awaiting-input' ? 'border-amber-400/60 glow-amber' :
        node.status === 'done' ? 'border-emerald-400/40' :
        node.status === 'failed' ? 'border-rose-500/60 node-flash-fail' :
        node.status === 'rejected' ? 'border-rose-400/30' :
        'border-border/80'
      } ${node.status === 'running' ? 'pulse-ring glow-cyan' : ''}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] text-cyan-300">
            {node.taskId}
          </span>
          <span className={`rounded border px-1.5 py-0.5 text-[9px] font-bold tracking-wider ${badge.cls}`}>
            {badge.label}{node.attempts > 1 ? ` ×${node.attempts}` : ''}
          </span>
        </div>
        {node.status === 'running' && <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-300" />}
        {node.status === 'reviewing' && <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-300" />}
        {node.status === 'done' && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" />}
        {(node.status === 'failed' || node.status === 'rejected') &&
          <XCircle className="h-3.5 w-3.5 text-rose-400" />}
      </div>

      <div className="mt-2 text-[12px] leading-snug text-foreground/90 min-h-[32px]">
        {node.status === 'queued' ? (
          <DecryptedText
            text={node.description}
            animateOn="view"
            sequential
            revealDirection="start"
            speed={18}
            className="reveal-char"
            encryptedClassName="opacity-40"
          />
        ) : (
          <span className="line-clamp-3">{node.description}</span>
        )}
      </div>

      {hasModel && (
        <div className="mt-1.5 flex items-center gap-1 text-[10px] text-cyan-300/80">
          <Cpu className="h-3 w-3" />
          <span className="font-mono truncate">{node.activeModel}</span>
        </div>
      )}

      {node.tools.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {node.tools.slice(-3).map((t, i) => (
            <span key={i} className="inline-flex items-center gap-0.5 rounded bg-amber-400/10 border border-amber-400/25 px-1 py-px text-[9px] text-amber-200">
              <Wrench className="h-2.5 w-2.5" /> {t.name}
            </span>
          ))}
          {node.tools.length > 3 && (
            <span className="text-[9px] text-muted-foreground">+{node.tools.length - 3}</span>
          )}
        </div>
      )}

      {node.retryReason && node.status === 'running' && (
        <p className="mt-1 truncate text-[9px] text-rose-300/80">{node.retryReason}</p>
      )}
      {node.status === 'failed' && node.error && (
        <p className="mt-1 line-clamp-2 text-[9px] text-rose-300/90">{node.error}</p>
      )}
      {node.status === 'awaiting-input' && node.question && (
        <AwaitingInputBlock
          question={node.question}
          inputContext={node.inputContext}
          onSend={t => data.onFeedback?.(node.taskId, t)}
        />
      )}
      {data.reviewMode && (
        <div className="mt-2">
          <ModelSelect
            value={node.activeModel}
            models={data.models || []}
            onChange={v => data.onAssign?.(node.taskId, v)}
          />
        </div>
      )}
    </div>
  );

  if (node.status === 'running') {
    return (
      <BorderGlow borderRadius={14} glowRadius={18} glowIntensity={0.9} colors={['#00f0ff', '#0ea5e9', '#22d3ee']}>
        {inner}
      </BorderGlow>
    );
  }
  return inner;
}

export function SlotCard({ slotId, model, onAssign }: {
  slotId: string;
  model: string | null;
  onAssign: (slot: string, model: string) => void;
}) {
  const [over, setOver] = useState(false);
  const assigned = !!model && model !== 'auto';
  return (
    <div
      onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={e => {
        e.preventDefault();
        setOver(false);
        const m = e.dataTransfer.getData('text/plain');
        if (m) onAssign(slotId, m);
      }}
      className={`pointer-events-auto flex h-[92px] w-[200px] flex-col items-center justify-center gap-1 rounded-xl border transition-all ${
        over ? 'border-cyan-400 ring-2 ring-cyan-400/70' :
        assigned ? 'border-cyan-400/60 glow-cyan' : 'border-border/80'
      } ${assigned ? 'bg-cyan-400/10' : 'bg-card/80'}`}
    >
      <span className="font-mono text-xs text-cyan-300">{slotId}</span>
      {assigned ? (
        <>
          <span className="max-w-[180px] truncate px-2 font-mono text-[11px] font-semibold text-cyan-100" title={model ?? undefined}>
            {model}
          </span>
          <span className="rounded-full border border-cyan-400/50 bg-cyan-400/15 px-1.5 text-[8px] font-bold tracking-widest text-cyan-200">
            ASSIGNED
          </span>
        </>
      ) : (
        <span className="text-[11px] text-muted-foreground">drop model here</span>
      )}
    </div>
  );
}

function SlotBody({ data }: { data: TaskCanvasNode }) {
  const slot = data.slotId!;
  const model = data.slotModel && data.slotModel !== 'auto' ? data.slotModel : null;
  const [over, setOver] = useState(false);
  return (
    <PixelCard variant="cyan" className={`h-[86px] w-[190px] ${over ? 'border-cyan-400/80' : ''}`}>
      <div
        // nodrag/nopan stop React Flow from swallowing the HTML5 drag events
        className={`nodrag nopan flex h-full w-full flex-col items-center justify-center gap-1 rounded-xl transition-colors ${
          model ? 'bg-cyan-400/10' : 'bg-card/80'
        } ${over ? 'ring-2 ring-cyan-400/80' : ''}`}
        onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={e => {
          e.preventDefault();
          e.stopPropagation();
          setOver(false);
          const m = e.dataTransfer.getData('text/plain');
          if (m) data.onAssign?.(slot, m);
        }}
      >
        <span className="font-mono text-[11px] text-cyan-300">{slot}</span>
        {model ? (
          <>
            <span className="max-w-[170px] truncate px-2 font-mono text-[10px] font-semibold text-cyan-200" title={model}>
              {model}
            </span>
            <span className="rounded-full border border-cyan-400/50 bg-cyan-400/15 px-1.5 text-[8px] font-bold tracking-widest text-cyan-200">
              ASSIGNED
            </span>
          </>
        ) : (
          <span className="text-[10px] text-muted-foreground">drop model</span>
        )}
      </div>
    </PixelCard>
  );
}

const TaskCanvasNodeComponent = memo(function TaskCanvasNodeComponent(props: NodeProps) {
  const data = props.data as unknown as TaskCanvasNode;
  return data.kind === 'slot'
    ? <SlotBody data={data} />
    : <TaskBody data={data} />;
});

export function FlowTaskNode(props: NodeProps) {
  const data = props.data as unknown as TaskCanvasNode;
  const isTask = data.kind === 'task';
  return (
    <>
      {isTask && data.node && data.node.dependsOn.length > 0 && (
        <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-cyan-400/70" />
      )}
      <TaskCanvasNodeComponent {...props} />
      {isTask && (
        <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-cyan-400/70" />
      )}
    </>
  );
}
