import { memo, useEffect, useRef } from 'react';
import type { ConsoleState, EventLine } from '@/console/useOrchestration';
import { fmtTokens } from '@/console/TaskCanvasNode';
import { fmtDuration } from '@/console/StatusRail';

const tokSum = (d: Record<string, any>) =>
  (Number(d.prompt_tokens) || 0) + (Number(d.completion_tokens) || 0);

function describe(e: EventLine): { cls: string; text: string } {
  const d = e.data;
  const t = new Date(e.at).toLocaleTimeString(undefined, { hour12: false });
  switch (e.type) {
    case 'task_started': return { cls: 'text-cyan-300', text: `[${String(d.task_id).toUpperCase()}] START ${d.description ?? ''}` };
    case 'tool_execution': return { cls: 'text-amber-300', text: `[${String(d.task_id).toUpperCase()}] TOOL ${d.tool_name} ${JSON.stringify(d.arguments ?? {}).slice(0, 70)}` };
    case 'task_worker_completed': return { cls: 'text-sky-300', text: `[${String(d.task_id).toUpperCase()}] OUTPUT (${(d.output ?? '').length} ch, ${fmtTokens(tokSum(d))} tok)` };
    case 'task_review_passed': return { cls: 'text-emerald-300', text: `[${String(d.task_id).toUpperCase()}] REVIEW PASS (${fmtTokens(tokSum(d))} tok)` };
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
    case 'run_completed': {
      const tot = (d.summary || {}).total_tokens || {};
      const sum = (tot.prompt || 0) + (tot.completion || 0);
      return { cls: 'text-emerald-300', text: `RUN COMPLETE — FINAL OUTPUT READY${sum ? ` · ${fmtTokens(sum)} tokens` : ''}` };
    }
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
  const scrollRef = useRef<HTMLDivElement>(null);

  const nodes = Object.values(state.nodes);
  const done = nodes.filter(n => n.status === 'done').length;
  const failed = nodes.filter(n => n.status === 'failed' || n.status === 'rejected').length;
  const running = nodes.filter(n => n.status === 'running' || n.status === 'reviewing').length;

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [log.length]);

  // Docked left-rail panel (M23): always visible, auto-scrolling event log.
  return (
    <section className="rail-panel flex min-h-[260px] flex-1 flex-col">
      <div className="flex items-center gap-2">
        <h2 className="rail-title flex-1">Event Log</h2>
        <span className={`mr-3 h-1.5 w-1.5 rounded-full ${isConnected ? 'bg-emerald-400' : 'bg-rose-500'}`} />
      </div>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-3 pb-2">
        <LogRows log={log} />
      </div>
      <div className="flex gap-3 border-t border-cyan-400/15 px-3 py-1.5 font-mono text-[9px] uppercase tracking-wider">
        <span className="text-muted-foreground">{state.eventCount} ev</span>
        <span className="text-cyan-300">{running} act</span>
        <span className="text-emerald-300">{done} pass</span>
        <span className="text-rose-300">{failed} fail</span>
        <span className="ml-auto text-muted-foreground">{fmtDuration(elapsedMs)}</span>
      </div>
    </section>
  );
}
