import { useMemo, useState } from 'react';
import { RotateCw, Search } from 'lucide-react';
import { ProviderLogo } from '@/console/providerLogos';

/* M23 right rail — the model catalogue docked as a MODELS panel (was the
   sliding ModelBay drawer), plus honest PROVIDER STATUS and NETWORK blocks
   built from real fetch/link state. */

export function ModelsPanel({
  models, modelTags, providerTag, loading, bayError, onRefresh
}: {
  models: string[];
  modelTags: Record<string, string>;
  providerTag: string | null;
  loading: boolean;
  bayError: string | null;
  onRefresh: () => void;
}) {
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

  return (
    <section className="rail-panel flex min-h-0 flex-[3] flex-col">
      <div className="flex items-center gap-2">
        <h2 className="rail-title flex-1">
          Models <span className="text-cyan-400/70">· {models.length}</span>
        </h2>
        <button
          onClick={onRefresh}
          disabled={loading}
          aria-label="Refresh model list"
          title="Re-fetch models for the current keys"
          className="mr-2 rounded p-1 text-cyan-300 hover:bg-cyan-900/40 disabled:opacity-50"
        >
          <RotateCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {bayError && (
        <p className="mx-3 mb-2 rounded-sm border border-rose-500/60 bg-rose-950/60 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-rose-300">
          {bayError}
        </p>
      )}

      <div className="flex gap-1.5 px-3 pb-2">
        <div className="relative min-w-0 flex-1">
          <Search className="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search…"
            className="h-7 w-full rounded border border-input bg-secondary/60 pl-7 pr-2 text-[11px] text-foreground placeholder:text-muted-foreground/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
        </div>
        <select
          value={orgFilter}
          onChange={e => setOrgFilter(e.target.value)}
          className="h-7 w-28 rounded border border-input bg-secondary/60 px-1 text-[11px] text-foreground focus-visible:outline-none"
        >
          <option value="all">All orgs</option>
          {orgs.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>

      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-3 pb-3">
        {models.length === 0 && !bayError && (
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            No models loaded. Add a key in ▸ KEYS, then refresh — drag a model
            onto a task slot to pre-assign it.
          </p>
        )}
        {models.length > 0 && visible.length === 0 && (
          <p className="text-[11px] text-muted-foreground">No models match the filters.</p>
        )}
        {visible.map(({ id, name }) => {
          const tag = modelTags[id] ?? providerTag ?? 'API';
          return (
            <div
              key={id}
              draggable
              onDragStart={e => {
                e.dataTransfer.setData('text/plain', id);
                e.dataTransfer.effectAllowed = 'copy';
              }}
              title={id}
              className="flex cursor-grab items-center gap-2 rounded-md border border-cyan-400/20 bg-cyan-400/5 px-2 py-1.5 hover:bg-cyan-900/30 active:cursor-grabbing"
            >
              <ProviderLogo modelId={id} tag={tag} size={26} />
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-[11px] text-cyan-100">{name}</p>
                <p className="truncate font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                  {tag} · {id.includes('/') ? id.slice(0, id.indexOf('/')) : 'catalog'}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// One row per account/tag that served models — real state, no invented
// uptime: ONLINE = contributed models to the current catalogue.
export function ProviderStatusPanel({ modelTags, bayError }: {
  modelTags: Record<string, string>;
  bayError: string | null;
}) {
  const rows = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const tag of Object.values(modelTags)) counts[tag] = (counts[tag] || 0) + 1;
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [modelTags]);

  return (
    <section className="rail-panel">
      <h2 className="rail-title">Provider Status</h2>
      <div className="space-y-1 px-3 pb-2.5">
        {rows.length === 0 && (
          <p className="font-mono text-[10px] text-muted-foreground">
            {bayError ? 'LAST FETCH FAILED' : 'No keys loaded — server default only.'}
          </p>
        )}
        {rows.map(([tag, count]) => (
          <p key={tag} className="rail-row justify-between">
            <span className="text-muted-foreground">{tag}</span>
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_hsl(144_100%_50%/0.9)]" />
              <span className="text-emerald-300">ONLINE · {count}</span>
            </span>
          </p>
        ))}
      </div>
    </section>
  );
}

export function NetworkPanel({ isConnected, modelCount, eventCount }: {
  isConnected: boolean;
  modelCount: number;
  eventCount: number;
}) {
  return (
    <section className="rail-panel">
      <h2 className="rail-title">Network</h2>
      <div className="space-y-0.5 px-3 pb-2.5">
        <p className="rail-row justify-between">
          <span className="text-muted-foreground">SOCKET:</span>
          <span className={isConnected ? 'text-emerald-300' : 'text-rose-400'}>
            {isConnected ? 'LINKED' : 'DOWN'}
          </span>
        </p>
        <p className="rail-row justify-between">
          <span className="text-muted-foreground">SERVER:</span>
          <span className="text-foreground/90">{window.location.hostname || '127.0.0.1'}:8100</span>
        </p>
        <p className="rail-row justify-between">
          <span className="text-muted-foreground">MODELS:</span>
          <span className="text-cyan-300">{modelCount}</span>
        </p>
        <p className="rail-row justify-between">
          <span className="text-muted-foreground">EVENTS:</span>
          <span className="text-cyan-300">{eventCount}</span>
        </p>
      </div>
    </section>
  );
}
