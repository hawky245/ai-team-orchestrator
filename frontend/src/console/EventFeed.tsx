import { memo, useEffect, useRef, useState } from 'react';
import { ChevronUp, ChevronDown } from 'lucide-react';
import type { ConsoleState, EventLine } from '@/console/useOrchestration';

function describe(e: EventLine): { cls: string; text: string } {
  const d = e.data;
  const t = new Date(e.at).toLocaleTimeString(undefined, { hour12: false });
  switch (e.type) {
    case 'task_started': return { cls: 'text-cyan-300', text: `[${String(d.task_id).toUpperCase()}] START ${d.description ?? ''}` };
    case 'tool_execution': return { cls: 'text-amber-300', text: `[${String(d.task_id).toUpperCase()}] TOOL ${d.tool_name} ${JSON.stringify(d.arguments ?? {}).slice(0, 70)}` };
    case 'task_worker_completed': return { cls: 'text-sky-300', text: `[${String(d.task_id).toUpperCase()}] OUTPUT (${(d.output ?? '').length} ch)` };
    case 'task_review_passed': return { cls: 'text-emerald-300', text: `[${String(d.task_id).toUpperCase()}] REVIEW PASS` };
    case 'task_review_failed': return { cls: 'text-rose-300', text: `[${String(d.task_id).toUpperCase()}] REVIEW FAIL: ${(d.feedback ?? d.error ?? '').slice(0, 80)}` };
    case 'task_retry': return { cls: 'text-rose-300', text: `[${String(d.task_id).toUpperCase()}] RETRY #${d.attempt}: ${(d.reason ?? '').slice(0, 70)}` };
    case 'task_requires_input': return { cls: 'text-amber-300', text: `[${String(d.task_id).toUpperCase()}] AWAITING INPUT` };
    case 'task_resumed': return { cls: 'text-emerald-300', text: `[${String(d.task_id).toUpperCase()}] RESUMED with feedback` };
    case 'task_input_timeout': return { cls: 'text-rose-400', text: `[${String(d.task_id).toUpperCase()}] INPUT TIMEOUT → failed` };
    case 'task_worker_failed': return { cls: 'text-rose-400', text: `[${String(d.task_id).toUpperCase()}] WORKER FAILED: ${(d.error ?? '').slice(0, 80)}` };
    case 'task_fallback_exhausted': return { cls: 'text-rose-400', text: `[${String(d.task_id).toUpperCase()}] FALLBACK EXHAUSTED` };
    case 'planning_completed': return { cls: 'text-cyan-200', text: `PLAN: ${d.task_count} tasks` };
    case 'model_selection': return { cls: 'text-cyan-200', text: `MODEL FORCE: all tasks → ${d.model}` };
    case 'plan_models_applied': return { cls: 'text-cyan-200', text: `PRE-ASSIGN: ${d.assigned} slot(s) set` };
    case 'plan_review_requested': return { cls: 'text-amber-300', text: `REVIEW: plan awaiting assignment` };
    case 'plan_review_completed': return { cls: 'text-emerald-300', text: `REVIEW: execution started (${d.assigned} assigned)` };
    case 'run_completed': return { cls: 'text-emerald-300', text: `RUN COMPLETE — FINAL OUTPUT READY` };
    case 'execution_failed': case 'planning_failed': return { cls: 'text-rose-400', text: `${e.type.toUpperCase()}: ${(d.error ?? '').slice(0, 90)}` };
    default: return { cls: 'text-muted-foreground', text: e.type };
  }
}

const LogRows = memo(function LogRows({ log }: { log: EventLine[] }) {
  const tail = log.length > 250 ? log.slice(log.length - 250) : log;
  return (
    <div className="space-y-px font-mono text-[11px] leading-relaxed">
      {tail.map(e => {
        const { cls, text } = describe(e);
        return (
          <div key={e.seq} className="flex gap-2">
            <span className="shrink-0 text-muted-foreground/60">{new Date(e.at).toLocaleTimeString(undefined, { hour12: false })}</span>
            <span className={cls}>{text}</span>
          </div>
        );
      })}
      {log.length > 250 && (
        <div className="text-muted-foreground/50">… {log.length - 250} earlier events truncated</div>
      )}
    </div>
  );
});

export function EventFeed({
  log, state, isConnected, elapsedMs
}: {
  log: EventLine[];
  state: ConsoleState;
  isConnected: boolean;
  elapsedMs: number;
}) {
  const [open, setOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const nodes = Object.values(state.nodes);
  const done = nodes.filter(n => n.status === 'done').length;
  const failed = nodes.filter(n => n.status === 'failed' || n.status === 'rejected').length;
  const running = nodes.filter(n => n.status === 'running' || n.status === 'reviewing').length;

  useEffect(() => {
    if (open && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [log.length, open]);

  return (
    <footer className="hud-panel absolute inset-x-3 bottom-3 z-40">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center gap-4 px-4 py-2 text-left"
      >
        <span className={`h-1.5 w-1.5 rounded-full ${isConnected ? 'bg-emerald-400' : 'bg-rose-500'}`} />
        <span className="hud-label">{isConnected ? 'SOCKET LINKED' : 'SOCKET DOWN'}</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {state.eventCount} events
        </span>
        <span className="font-mono text-[11px] text-cyan-300">{running} active</span>
        <span className="font-mono text-[11px] text-emerald-300">{done} passed</span>
        <span className="font-mono text-[11px] text-rose-300">{failed} failed</span>
        <span className="ml-auto font-mono text-[11px] text-muted-foreground">
          {(elapsedMs / 1000).toFixed(1)}s
        </span>
        {open
          ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
          : <ChevronUp className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && (
        <div ref={scrollRef} className="max-h-56 overflow-y-auto border-t border-border/60 px-4 py-2">
          <LogRows log={log} />
        </div>
      )}
    </footer>
  );
}
