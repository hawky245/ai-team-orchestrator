import { useMemo, useState } from 'react';
import { Zap, Radio, Play, X, RotateCw } from 'lucide-react';
import TextType from '@/components/reactbits/TextType';
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
      </div>
    </header>
  );
}

/* Slide-in Model Bay drawer: hidden by default, opens from a floating HUD
   tab. Chips are tagged with the serving API provider (from /api/models),
   carry a refresh button, and never display key material. */
export function ModelBay({ models, providerBadge, providerTag, modelTags, loading, bayError, onRefresh }: {
  models: string[];
  providerBadge: string | null;
  providerTag: string | null;
  modelTags?: Record<string, string>;
  loading: boolean;
  bayError: string | null;
  onRefresh: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [orgFilter, setOrgFilter] = useState('all');

  const parsed = useMemo(
    () =>
      models.map(m => {
        const i = m.indexOf('/');
        return i > 0 ? { id: m, org: m.slice(0, i), name: m.slice(i + 1) } : { id: m, org: 'other', name: m };
      }),
    [models]
  );
  const orgs = useMemo(() => Array.from(new Set(parsed.map(p => p.org))).sort(), [parsed]);
  const q = query.trim().toLowerCase();
  const visible = parsed.filter(
    p =>
      (orgFilter === 'all' || p.org === orgFilter) &&
      (!q || p.id.toLowerCase().includes(q))
  );

  const resetFilters = () => {
    setQuery('');
    setOrgFilter('all');
  };

  return (
    <>
      {/* Floating HUD tab, pinned to the left edge; slides along with drawer */}
      <button
        onClick={() => setOpen(o => !o)}
        aria-label="Toggle model bay"
        className={`fixed left-0 top-1/3 z-[45] rounded-r-lg border border-l-0 border-cyan-400/40 bg-cyan-950/80 px-2 py-3 text-[10px] font-bold tracking-[0.2em] text-cyan-200 backdrop-blur-md transition-transform duration-300 ease-in-out hover:bg-cyan-900/60 ${
          open ? 'translate-x-72' : 'translate-x-0'
        }`}
      >
        {open ? '◂' : '▸'} BAY · {models.length}
      </button>

      <aside
        aria-hidden={!open}
        className={`fixed top-0 left-0 z-[45] flex h-full w-72 flex-col border-r border-cyan-800/50 bg-[hsl(223_33%_5%)]/95 backdrop-blur-md transition-transform duration-300 ease-in-out ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex items-center justify-between gap-2 border-b border-cyan-900/40 px-3 py-3">
          <p className="hud-label min-w-0 truncate">
            Model Bay <span className="text-cyan-300">·</span> {models.length}
            {visible.length !== models.length && (
              <span className="ml-1 text-amber-300/90">→ {visible.length}</span>
            )}
          </p>
          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={onRefresh}
              disabled={loading}
              aria-label="Refresh model list"
              title="Re-fetch models for the current key"
              className="rounded p-1 text-cyan-300 hover:bg-cyan-900/40 disabled:opacity-50"
            >
              <RotateCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close model bay"
              className="rounded p-1 text-muted-foreground hover:bg-cyan-900/40 hover:text-cyan-100"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {providerBadge && (
          <div className="flex flex-wrap items-center gap-2 px-3 pt-2.5">
            <span className="inline-block rounded-sm border border-cyan-700/50 bg-cyan-950/90 px-2 py-1 font-mono text-xs uppercase tracking-wider text-cyan-400">
              {providerBadge}
            </span>
          </div>
        )}
        {bayError && (
          <div className="px-3 pt-2">
            <span className="inline-block rounded-sm border border-rose-500/60 bg-rose-950/60 px-2 py-1 font-mono text-xs uppercase tracking-wider text-rose-300">
              {bayError}
            </span>
          </div>
        )}

        <div className="space-y-2 border-b border-cyan-900/40 px-3 py-2">
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search models…"
            className="h-8 w-full rounded-md border border-input bg-secondary/60 px-2 text-xs text-foreground placeholder:text-muted-foreground/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          <div className="flex gap-1.5">
            <select
              value={orgFilter}
              onChange={e => setOrgFilter(e.target.value)}
              className="h-8 min-w-0 flex-1 rounded-md border border-input bg-secondary/60 px-1.5 text-xs text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="all">All orgs ({orgs.length})</option>
              {orgs.map(o => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
            {(query || orgFilter !== 'all') && (
              <button
                onClick={resetFilters}
                className="h-8 shrink-0 rounded-md border border-cyan-400/30 px-2 text-[10px] font-bold tracking-wide text-cyan-200 hover:bg-cyan-900/40"
              >
                RESET
              </button>
            )}
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-3">
          {models.length === 0 && !bayError && (
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              No models loaded. Enter a key (optional) and press ↻ or FETCH,
              then drag a model onto a task slot.
            </p>
          )}
          {models.length > 0 && visible.length === 0 && (
            <p className="text-[11px] text-muted-foreground">
              No models match the current filters.
            </p>
          )}
          {visible.map(({ id, name }) => (
            <div
              key={id}
              draggable
              onDragStart={e => {
                e.dataTransfer.setData('text/plain', id);
                e.dataTransfer.effectAllowed = 'copy';
                // Slide the drawer out of the way mid-drag: it is fixed and
                // z-[45], so left open it covers the leftmost task slots and
                // swallows the drop before it ever reaches them.
                setOpen(false);
              }}
              onDragEnd={() => {
                // It was the drag that auto-retracted the bay, so the drag
                // (drop OR cancel) brings it back. Manual closes stay closed
                // — nothing else ever sets open=false while a chip is held.
                setOpen(true);
              }}
              title={id}
              className="cursor-grab rounded-md border border-cyan-400/25 bg-cyan-400/5 px-2 py-1.5 hover:bg-cyan-900/30 active:cursor-grabbing"
            >
              <span className="mb-1 block font-mono text-sm text-cyan-100 truncate">{name}</span>
              {/* Badge = the API account/key that will serve this model
                  (per-chip in merged multi-key bays; never key material). */}
              <span className="inline-block rounded-sm border border-cyan-700/50 bg-cyan-950/90 px-1.5 py-0.5 font-mono text-[10px] uppercase text-cyan-400">
                {modelTags?.[id] ?? providerTag ?? 'API'} · {id.includes('/') ? id.slice(0, id.indexOf('/')) : id}
              </span>
            </div>
          ))}
        </div>

        <p className="border-t border-cyan-900/40 px-3 py-2 text-[9px] leading-snug text-muted-foreground">
          Drag onto a slot (pre-flight) or a task node (review mode).
        </p>
      </aside>
    </>
  );
}
