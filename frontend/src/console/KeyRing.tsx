import { useMemo, useState } from 'react';
import { KeyRound, Trash2, Check, X, Download, Loader2 } from 'lucide-react';

export interface StoredKey {
  id: string;
  label: string;
  key: string;
  provider: string;
  addedAt: number;
}

export const RING_LS = 'orchestrator_keyring';

// Key prefixes only used to LABEL a stored key's provider; mirrors the
// backend's detect_provider_label. No key material is ever sent anywhere
// from this component beyond the parent's existing fetch path.
export function detectProvider(key: string): string {
  if (key.startsWith('nvapi-')) return 'NVIDIA';
  if (key.startsWith('sk-or-')) return 'OPENROUTER';
  if (key.startsWith('gsk_')) return 'GROQ';
  if (key.startsWith('sk-ant-')) return 'ANTHROPIC';
  if (key.startsWith('AIza')) return 'GEMINI';
  if (key.startsWith('sk-')) return 'OPENAI';
  return 'CUSTOM';
}

// What a badge should read for a stored key: the user's own label when
// present, otherwise the detected provider name.
export function displayTag(entry: StoredKey): string {
  return entry.label?.trim() ? entry.label.trim() : entry.provider;
}

export function loadRing(): StoredKey[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(RING_LS) ?? '[]');
    if (Array.isArray(parsed)) {
      return parsed.filter(
        k => k && typeof k.key === 'string' && typeof k.label === 'string'
      );
    }
  } catch { /* corrupted -> clean */ }
  return [];
}

function maskKey(key: string): string {
  if (key.length <= 10) return '••••';
  return `${key.slice(0, 6)}…${key.slice(-4)}`;
}

export function KeyRing({
  keys, activeKey, loading, onAddKey, onUseKey, onRemoveKey
}: {
  keys: StoredKey[];
  activeKey: string;
  loading: boolean;
  onAddKey: (entry: StoredKey) => void;
  onUseKey: (key: string) => void;
  onRemoveKey: (entry: StoredKey) => void;
}) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState('');
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  const addKey = (activate = false) => {
    const k = value.trim();
    if (!k) { setError('Enter a key value'); return; }
    if (keys.some(e => e.key === k)) { setError('Already stored'); return; }
    const provider = detectProvider(k);
    const entry: StoredKey = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      label: label.trim(),
      key: k,
      provider,
      addedAt: Date.now()
    };
    onAddKey(entry);
    setLabel('');
    setValue('');
    setError(null);
    if (activate) onUseKey(k);
  };

  const removeKey = (entry: StoredKey) => onRemoveKey(entry);

  const sorted = useMemo(
    () => [...keys].sort((a, b) => b.addedAt - a.addedAt),
    [keys]
  );

  return (
    <>
      <button
        onClick={() => setOpen(o => !o)}
        aria-label="Toggle api key ring"
        className={`fixed left-0 top-[52%] z-[45] rounded-r-lg border border-l-0 border-violet-400/40 bg-violet-950/80 px-2 py-3 text-[10px] font-bold tracking-[0.2em] text-violet-200 backdrop-blur-md transition-transform duration-300 ease-in-out hover:bg-violet-900/60 ${
          open ? 'translate-x-72' : 'translate-x-0'
        }`}
      >
        {open ? '◂' : '▸'} KEYS · {keys.length}
      </button>

      <aside
        aria-hidden={!open}
        className={`fixed top-0 left-0 z-[46] flex h-full w-72 flex-col border-r border-violet-800/50 bg-[hsl(223_33%_5%)]/95 backdrop-blur-md transition-transform duration-300 ease-in-out ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex items-center justify-between gap-2 border-b border-violet-900/40 px-3 py-3">
          <p className="hud-label flex items-center gap-1.5 text-violet-300">
            <KeyRound className="h-3.5 w-3.5" /> API Key Ring
          </p>
          <button
            onClick={() => setOpen(false)}
            aria-label="Close key ring"
            className="rounded p-1 text-muted-foreground hover:bg-violet-900/40 hover:text-violet-100"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-2 border-b border-violet-900/40 p-3">
          <input
            value={label}
            onChange={e => setLabel(e.target.value)}
            placeholder="Label (e.g. work groq)"
            className="h-8 w-full rounded-md border border-input bg-secondary/60 px-2 text-xs text-foreground placeholder:text-muted-foreground/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          <input
            value={value}
            onChange={e => { setValue(e.target.value); setError(null); }}
            onKeyDown={e => { if (e.key === 'Enter') addKey(); }}
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="sk-… / gsk_… / nvapi-…"
            className="h-8 w-full rounded-md border border-input bg-secondary/60 px-2 font-mono text-xs text-foreground placeholder:text-muted-foreground/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          {error && <p className="text-[10px] text-rose-300">{error}</p>}
          <div className="flex gap-1.5">
            <button
              onClick={() => addKey()}
              disabled={loading}
              className="flex-1 rounded-md border border-violet-400/40 bg-violet-400/10 py-1.5 text-[11px] font-bold tracking-wide text-violet-200 hover:bg-violet-400/20 disabled:opacity-50"
            >
              + STORE KEY
            </button>
            <button
              onClick={() => addKey(true)}
              disabled={loading}
              title="Store, make active and fetch this provider's models"
              className="flex flex-1 items-center justify-center gap-1 rounded-md border border-cyan-400/40 bg-cyan-400/10 py-1.5 text-[11px] font-bold tracking-wide text-cyan-200 hover:bg-cyan-400/20 disabled:opacity-50"
            >
              {loading
                ? <Loader2 className="h-3 w-3 animate-spin" />
                : <Download className="h-3 w-3" />}
              STORE & FETCH
            </button>
          </div>
          <p className="text-[9px] leading-snug text-muted-foreground">
            Stored in this browser&apos;s localStorage like the active key field —
            anyone with access to this machine can read them.
          </p>
        </div>

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {keys.length === 0 && (
            <p className="text-[11px] text-muted-foreground">No stored keys yet.</p>
          )}
          {sorted.map(entry => {
            const active = entry.key === activeKey;
            return (
              <div
                key={entry.id}
                className={`rounded-md border p-2 ${
                  active ? 'border-cyan-400/50 bg-cyan-400/5' : 'border-border/70 bg-card/60'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-xs font-semibold text-foreground">
                    {entry.label?.trim() || entry.provider.toLowerCase() + ' key'}
                  </span>
                  <span className="shrink-0 rounded-sm border border-cyan-700/50 bg-cyan-950/90 px-1.5 py-0.5 font-mono text-[9px] uppercase text-cyan-400">
                    {displayTag(entry)}
                  </span>
                </div>
                <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                  {maskKey(entry.key)}
                </p>
                <div className="mt-1.5 flex items-center gap-1.5">
                  <button
                    onClick={() => onUseKey(entry.key)}
                    disabled={active || loading}
                    title="Make active and fetch this provider's models"
                    className="flex items-center gap-1 rounded border border-cyan-400/40 px-2 py-0.5 text-[10px] font-bold text-cyan-200 hover:bg-cyan-400/10 disabled:opacity-40"
                  >
                    {loading && !active
                      ? <Loader2 className="h-3 w-3 animate-spin" />
                      : <Check className="h-3 w-3" />}
                    {active ? 'ACTIVE' : 'USE & FETCH'}
                  </button>
                  <button
                    onClick={() => removeKey(entry)}
                    aria-label={`Remove ${entry.label}`}
                    className="flex items-center gap-1 rounded border border-rose-400/40 px-2 py-0.5 text-[10px] font-bold text-rose-300 hover:bg-rose-400/10"
                  >
                    <Trash2 className="h-3 w-3" /> REMOVE
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}
