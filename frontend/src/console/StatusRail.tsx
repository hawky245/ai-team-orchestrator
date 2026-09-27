import { Radio } from 'lucide-react';
import type { ConsoleState } from '@/console/useOrchestration';

export function Wordmark() {
  return (
    <div className="px-1 pt-1">
      <h1 className="flex items-center gap-2 font-mono text-[26px] font-black tracking-[0.12em] text-cyan-300 neon-text">
        ORCHESTRATOR
        <Radio className="h-5 w-5" />
      </h1>
      <p className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.28em] text-muted-foreground">
        AI team pipeline // plan · execute · review · repeat
      </p>
    </div>
  );
}

function pad(n: number) {
  return String(Math.floor(n)).padStart(2, '0');
}

export function fmtDuration(ms: number): string {
  const s = Math.max(0, ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  return `${pad(h)}:${pad(m)}:${pad(sec)}`;
}

const PHASE_TEXT: Record<string, { label: string; cls: string }> = {
  idle: { label: 'STANDBY', cls: 'text-muted-foreground' },
  planning: { label: 'PLANNING', cls: 'text-cyan-300' },
  'awaiting-review': { label: 'AWAITING REVIEW', cls: 'text-amber-300' },
  running: { label: 'RUNNING', cls: 'text-emerald-400' },
  done: { label: 'COMPLETE', cls: 'text-emerald-400' },
  failed: { label: 'FAULT', cls: 'text-rose-400' }
};

export function StatusPanel({ state, elapsedMs }: { state: ConsoleState; elapsedMs: number }) {
  const nodes = Object.values(state.nodes);
  const done = nodes.filter(n => n.status === 'done').length;
  const failed = nodes.filter(n => n.status === 'failed' || n.status === 'rejected').length;
  const total = nodes.length;
  const phase = PHASE_TEXT[state.phase];
  // Segmented bar: one segment per planned task, colored by outcome.
  const segments = state.order.map(id => {
    const st = state.nodes[id]?.status;
    if (st === 'done') return 'bg-cyan-400 shadow-[0_0_6px_hsl(184_100%_50%/0.8)]';
    if (st === 'failed' || st === 'rejected') return 'bg-rose-500';
    if (st === 'running' || st === 'reviewing' || st === 'awaiting-input') return 'bg-cyan-400/50';
    return 'bg-cyan-900/60';
  });

  return (
    <section className="rail-panel">
      <h2 className="rail-title">System Status</h2>
      <div className="space-y-0.5 px-3 pb-2">
        <p className="rail-row">
          <span className="text-muted-foreground">&gt; PIPELINE:</span>
          <span className={`font-bold ${phase.cls}`}>{phase.label}</span>
        </p>
        <p className="rail-row">
          <span className="text-muted-foreground">&gt; EXECUTION ID:</span>
          <span className="text-foreground/90">{state.runIdHint ? state.runIdHint.slice(0, 8) : '—'}</span>
        </p>
        <p className="rail-row">
          <span className="text-muted-foreground">&gt; STARTED:</span>
          <span className="text-foreground/90">
            {state.startedAt ? new Date(state.startedAt).toLocaleTimeString(undefined, { hour12: false }) : '—'}
          </span>
        </p>
        <p className="rail-row">
          <span className="text-muted-foreground">&gt; DURATION:</span>
          <span className="text-foreground/90">{state.startedAt ? fmtDuration(elapsedMs) : '—'}</span>
        </p>
        <p className="rail-row">
          <span className="text-muted-foreground">&gt; STATUS:</span>
          <span className="text-foreground/90">
            {done} / {total || '—'} COMPLETE
            {failed > 0 && <span className="ml-1 text-rose-400">· {failed} FAILED</span>}
          </span>
        </p>
        <div className="mt-1.5 flex gap-[3px]">
          {segments.length > 0
            ? segments.map((cls, i) => <span key={i} className={`h-3 flex-1 rounded-[2px] ${cls}`} />)
            : Array.from({ length: 12 }, (_, i) => (
                <span key={i} className="h-3 flex-1 rounded-[2px] bg-cyan-950/80" />
              ))}
        </div>
      </div>
    </section>
  );
}

// Operator-facing digest: what is blocked, what failed, what is retrying.
export function NotesPanel({ state }: { state: ConsoleState }) {
  const notes: string[] = [];
  for (const n of Object.values(state.nodes)) {
    if (n.status === 'awaiting-input')
      notes.push(`${n.taskId.toUpperCase()} is held for your input.`);
    if (n.status === 'failed' && n.error)
      notes.push(`${n.taskId.toUpperCase()} failed: ${n.error.slice(0, 90)}`);
    if (n.status === 'rejected' && n.feedback)
      notes.push(`${n.taskId.toUpperCase()} rejected: ${n.feedback.slice(0, 90)}`);
    if (n.retryReason && (n.status === 'running' || n.status === 'reviewing'))
      notes.push(`${n.taskId.toUpperCase()} rotating: ${n.retryReason.slice(0, 90)}`);
  }
  if (state.phase === 'idle') notes.push('All systems nominal — awaiting objective.');

  return (
    <section className="rail-panel">
      <h2 className="rail-title">Pipeline Notes</h2>
      <div className="max-h-36 space-y-1 overflow-y-auto px-3 pb-2.5">
        {notes.map((t, i) => (
          <p key={i} className="font-mono text-[10px] leading-snug text-muted-foreground">{t}</p>
        ))}
      </div>
    </section>
  );
}
