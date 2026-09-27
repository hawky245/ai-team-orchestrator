import { useEffect, useRef, useState } from 'react';
import { ApiService } from '@/services/api';
import type { ConsoleState } from '@/console/useOrchestration';

/* M23 bottom telemetry bar. CPU/MEM are real server process metrics from
   GET /api/telemetry; TOKENS/s is derived from the M22a token totals —
   nothing here is decorative fake data. */

const HISTORY = 32;

function Sparkline({ values, max }: { values: (number | null)[]; max: number }) {
  const pts = values
    .map((v, i) => (v === null ? null : `${(i / (HISTORY - 1)) * 100},${30 - (Math.min(v, max) / max) * 26}`))
    .filter((p): p is string => p !== null);
  if (pts.length < 2) return <svg viewBox="0 0 100 30" className="h-7 w-28" />;
  return (
    <svg viewBox="0 0 100 30" className="h-7 w-28 overflow-visible" preserveAspectRatio="none">
      <polyline
        points={pts.join(' ')}
        fill="none"
        stroke="hsl(184 100% 55%)"
        strokeWidth={1.6}
        style={{ filter: 'drop-shadow(0 0 3px hsl(184 100% 50% / 0.8))' }}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function Bars({ values }: { values: number[] }) {
  const max = Math.max(1, ...values);
  return (
    <div className="flex h-7 items-end gap-[2px]">
      {values.map((v, i) => (
        <span
          key={i}
          className="w-[3px] rounded-sm bg-cyan-400/80"
          style={{ height: `${Math.max(2, (v / max) * 26)}px`, opacity: 0.25 + 0.75 * (i / values.length) }}
        />
      ))}
    </div>
  );
}

function fmtUptime(s: number): string {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`;
}

export function TelemetryBar({ state }: { state: ConsoleState }) {
  const [cpu, setCpu] = useState<(number | null)[]>([]);
  const [mem, setMem] = useState<(number | null)[]>([]);
  const [tps, setTps] = useState<number[]>([]);
  const [uptime, setUptime] = useState(0);
  const lastTokRef = useRef(0);
  const lastTokAtRef = useRef(Date.now());

  useEffect(() => {
    let cancelled = false;
    const push = <T,>(arr: T[], v: T) => [...arr, v].slice(-HISTORY);
    const poll = async () => {
      try {
        const t = await ApiService.getTelemetry();
        if (cancelled) return;
        setCpu(prev => push(prev, t.cpu_percent));
        setMem(prev => push(prev, t.mem_percent));
        setUptime(t.uptime_s);
      } catch {
        /* server briefly unreachable — keep the last history */
      }
    };
    poll();
    const id = window.setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Tokens/s: delta of the run's cumulative token total since the last tick.
  useEffect(() => {
    const total = state.totalTokens.prompt + state.totalTokens.completion;
    const now = Date.now();
    const dt = Math.max(0.25, (now - lastTokAtRef.current) / 1000);
    const rate = total > lastTokRef.current ? (total - lastTokRef.current) / dt : 0;
    lastTokRef.current = total;
    lastTokAtRef.current = now;
    setTps(prev => [...prev, Math.round(rate)].slice(-HISTORY));
  }, [state.totalTokens]);

  const lastCpu = [...cpu].reverse().find(v => v !== null);
  const lastMem = [...mem].reverse().find(v => v !== null);
  const rateNow = tps[tps.length - 1] ?? 0;

  return (
    <footer className="rail-panel bracket-frame mx-3 mb-3 flex items-center gap-6 px-5 py-2">
      <div className="flex items-center gap-2.5">
        <div>
          <p className="font-mono text-[9px] uppercase tracking-widest text-muted-foreground">CPU</p>
          <p className="font-mono text-[12px] text-cyan-200">
            {lastCpu === undefined ? '—' : `${lastCpu.toFixed(0)}%`}
          </p>
        </div>
        <Sparkline values={cpu} max={100} />
      </div>
      <div className="flex items-center gap-2.5">
        <div>
          <p className="font-mono text-[9px] uppercase tracking-widest text-muted-foreground">MEM</p>
          <p className="font-mono text-[12px] text-cyan-200">
            {lastMem === undefined ? '—' : `${lastMem.toFixed(0)}%`}
          </p>
        </div>
        <Sparkline values={mem} max={100} />
      </div>
      <div className="flex items-center gap-2.5">
        <div>
          <p className="font-mono text-[9px] uppercase tracking-widest text-muted-foreground">TOKENS</p>
          <p className="font-mono text-[12px] text-cyan-200">
            {rateNow >= 1000 ? `${(rateNow / 1000).toFixed(1)}K` : rateNow}/s
          </p>
        </div>
        <Bars values={tps.length ? tps : Array(HISTORY).fill(0)} />
      </div>
      <div className="ml-auto text-right">
        <p className="font-mono text-[11px] font-bold tracking-[0.18em] text-cyan-300">
          ORCHESTRATOR <span className="text-muted-foreground">v2.3.0</span>
        </p>
        <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
          server up {fmtUptime(uptime)} · built for reliability, designed for intelligence
        </p>
      </div>
    </footer>
  );
}
