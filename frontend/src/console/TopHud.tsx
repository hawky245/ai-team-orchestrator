import { Zap, Radio, Play, X, Coins } from 'lucide-react';
import TextType from '@/components/reactbits/TextType';
import { fmtTokens } from '@/console/TaskCanvasNode';
import type { ConsolePhase } from '@/console/useOrchestration';

export interface TopHudProps {
  goal: string;
  setGoal: (v: string) => void;
  models: string[];
  selectedModel: string;
  setSelectedModel: (v: string) => void;
  reviewPlan: boolean;
  setReviewPlan: (v: boolean) => void;
  onSubmit: () => void;
  onStop: () => void;        // abandon the live run at the next boundary
  isConnected: boolean;
  phase: ConsolePhase;
  tokens: { prompt: number; completion: number };
  reviewReady: boolean;      // plan_review_requested received
  onStartReview: () => void; // send assignments and resume
  assignedCount: number;
}

const PHASE_LABEL: Record<ConsolePhase, string> = {
  idle: 'STANDBY',
  planning: 'PLANNING',
  'awaiting-review': 'AWAITING REVIEW',
  running: 'EXECUTING',
  done: 'COMPLETE',
  failed: 'FAULT'
};

const PHASE_COLOR: Record<ConsolePhase, string> = {
  idle: 'text-muted-foreground',
  planning: 'text-cyan-300',
  'awaiting-review': 'text-amber-300',
  running: 'text-cyan-300',
  done: 'text-emerald-300',
  failed: 'text-rose-400'
};

export function TopHud(props: TopHudProps) {
  const busy = props.phase === 'planning' || props.phase === 'running' || props.phase === 'awaiting-review';

  return (
    <header className="hud-panel sticky top-3 z-40 mx-3 mt-3 px-4 py-3">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-3">
        {/* Identity */}
        <div className="flex items-center gap-3 pr-2">
          <div className="relative grid h-9 w-9 place-items-center rounded-full border border-cyan-400/40 bg-cyan-400/5">
            <Radio className="h-4 w-4 text-cyan-300" />
            {busy && <span className="pulse-ring absolute inset-0 rounded-full" />}
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-[0.22em] text-foreground">ORCHESTRATOR</h1>
            <p className={`flex items-center gap-2 font-mono text-[10px] tracking-widest ${PHASE_COLOR[props.phase]}`}>
              <span
                className={`inline-block h-1.5 w-1.5 rounded-full ${
                  props.phase === 'running' || props.phase === 'planning'
                    ? 'bg-cyan-400 animate-pulse-dot'
                    : props.phase === 'awaiting-review'
                    ? 'bg-amber-400 animate-pulse-dot'
                    : props.phase === 'done'
                    ? 'bg-emerald-400'
                    : props.phase === 'failed'
                    ? 'bg-rose-500'
                    : 'bg-muted-foreground'
                }`}
              />
              {PHASE_LABEL[props.phase]}
            </p>
          </div>
        </div>

        {/* Goal prompt bar */}
        <div className="relative min-w-[260px] flex-1">
          <input
            value={props.goal}
            onChange={e => props.setGoal(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') props.onSubmit(); }}
            disabled={busy}
            placeholder={props.phase === 'idle' || props.phase === 'done' || props.phase === 'failed'
              ? 'State your objective…'
              : ''}
            className="h-10 w-full rounded-lg border border-cyan-400/20 bg-background/80 pl-10 pr-3 font-mono text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:border-cyan-400/50 focus:outline-none focus:ring-1 focus:ring-cyan-400/40 disabled:opacity-60"
          />
          <Zap className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-cyan-300/70" />
          {busy && (
            <div className="absolute inset-0 flex items-center rounded-lg bg-background/70 px-10">
              <TextType
                text={['AWAITING OPERATOR INPUT…', 'PIPELINE ACTIVE — STAND BY…']}
                typingSpeed={28}
                deletingSpeed={14}
                pauseDuration={1600}
                className="font-mono text-[12px] text-cyan-300/80"
                cursorCharacter="▍"
              />
            </div>
          )}
        </div>

        {/* Model strategy (keys live in the ▸ KEYS drawer, which also fetches) */}
        <select
          value={props.selectedModel}
          onChange={e => props.setSelectedModel(e.target.value)}
          disabled={busy || props.reviewPlan}
          title={props.reviewPlan ? 'Disabled: per-task review assignment is active' : 'Run-level model strategy'}
          className="h-10 w-44 rounded-md border border-input bg-secondary/60 px-2 text-[11px] text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
        >
          <option value="auto">Auto (planner routes)</option>
          {props.models.map(m => <option key={m} value={m}>Force {m}</option>)}
        </select>

        {/* Review-first toggle */}
        <label className="flex h-10 cursor-pointer select-none items-center gap-2 rounded-md border border-input bg-secondary/40 px-3 text-[11px] text-muted-foreground">
          <input
            type="checkbox"
            checked={props.reviewPlan}
            onChange={e => props.setReviewPlan(e.target.checked)}
            className="h-3.5 w-3.5 accent-cyan-400"
          />
          Review plan
        </label>

        {/* Primary trigger / review release */}
        {props.phase === 'awaiting-review' ? (
          <button
            onClick={props.onStartReview}
            className="flex h-10 items-center gap-2 rounded-lg bg-amber-400 px-5 text-[13px] font-bold tracking-wide text-black hover:bg-amber-300 glow-amber"
          >
            <Play className="h-4 w-4" /> START EXECUTION
            {props.assignedCount > 0 && (
              <span className="rounded bg-black/20 px-1.5 py-0.5 font-mono text-[10px]">
                {props.assignedCount} set
              </span>
            )}
          </button>
        ) : (
          <button
            onClick={props.onSubmit}
            disabled={!props.isConnected || busy || !props.goal.trim()}
            className="flex h-10 items-center gap-2 rounded-lg bg-cyan-400 px-5 text-[13px] font-bold tracking-wide text-black transition hover:bg-cyan-300 glow-cyan disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Zap className="h-4 w-4" /> ENGAGE
          </button>
        )}
        {busy && (
          <button
            onClick={props.onStop}
            className="flex h-10 items-center gap-2 rounded-lg border border-rose-500/60 bg-rose-500/10 px-4 text-[13px] font-bold tracking-wide text-rose-300 transition hover:bg-rose-500/25"
            title="Stop the run at the next task boundary"
          >
            <X className="h-4 w-4" /> STOP
          </button>
        )}
        {props.tokens.prompt + props.tokens.completion > 0 && (
          <span
            className="flex h-10 items-center gap-1.5 rounded-lg border border-violet-400/30 bg-violet-400/5 px-3 font-mono text-[11px] text-violet-300"
            title={`${props.tokens.prompt} prompt / ${props.tokens.completion} completion tokens this run`}
          >
            <Coins className="h-3.5 w-3.5" />
            {fmtTokens(props.tokens.prompt + props.tokens.completion)} TOK
          </span>
        )}
      </div>
    </header>
  );
}
