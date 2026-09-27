import { AnimatePresence, motion } from 'motion/react';
import { X, Clock3, RotateCcw, Wrench, Cpu, MessageSquareText, Zap } from 'lucide-react';
import SpotlightCard from '@/components/reactbits/SpotlightCard';
import TextType from '@/components/reactbits/TextType';
import { fmtTokens } from '@/console/TaskCanvasNode';
import type { TaskNode } from '@/console/useOrchestration';

const STATUS_STYLES: Record<string, string> = {
  queued: 'text-muted-foreground',
  running: 'text-cyan-300',
  reviewing: 'text-sky-300',
  'awaiting-input': 'text-amber-300',
  done: 'text-emerald-300',
  rejected: 'text-rose-300',
  failed: 'text-rose-400'
};

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border/70 bg-secondary/40 px-2.5 py-1.5">
      <span className="text-cyan-300/70">{icon}</span>
      <div>
        <p className="text-[9px] uppercase tracking-wider text-muted-foreground">{label}</p>
        <p className="font-mono text-[11px] text-foreground">{value}</p>
      </div>
    </div>
  );
}

export function InspectorDrawer({
  node, onClose
}: { node: TaskNode | null; onClose: () => void }) {
  return (
    <AnimatePresence>
      {node && (
        <motion.aside
          key="inspector"
          initial={{ x: 420, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: 420, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 300, damping: 30 }}
          className="absolute inset-y-16 right-3 z-40 w-[380px] max-w-[calc(100vw-24px)]"
        >
          <SpotlightCard className="h-full">
            <div className="flex h-full flex-col p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-mono text-[11px] text-cyan-300">{node.role ? node.role.toUpperCase() : node.taskId.toUpperCase()} · <span className={STATUS_STYLES[node.status]}>{node.status.toUpperCase()}</span></p>
                  <p className="mt-1 text-[13px] leading-snug text-foreground">{node.description}</p>
                </div>
                <button onClick={onClose} className="rounded p-1 text-muted-foreground hover:text-foreground">
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="mt-3 grid grid-cols-2 gap-2">
                <Metric
                  icon={<Clock3 className="h-3.5 w-3.5" />}
                  label="Elapsed"
                  value={node.startedAt ? `${(((node.endedAt ?? Date.now()) - node.startedAt) / 1000).toFixed(1)}s` : '—'}
                />
                <Metric
                  icon={<RotateCcw className="h-3.5 w-3.5" />}
                  label="Attempts"
                  value={String(Math.max(1, node.attempts))}
                />
                <Metric
                  icon={<Wrench className="h-3.5 w-3.5" />}
                  label="Tool calls"
                  value={String(node.tools.length)}
                />
                <Metric
                  icon={<Cpu className="h-3.5 w-3.5" />}
                  label="Model"
                  value={node.activeModel === 'auto' ? 'planner' : node.activeModel.replace(/^.*\//, '')}
                />
                <Metric
                  icon={<Zap className="h-3.5 w-3.5" />}
                  label="Tokens (in / out)"
                  value={`${fmtTokens(node.promptTokens ?? 0)} / ${fmtTokens(node.completionTokens ?? 0)}`}
                />
              </div>

              {node.dependsOn.length > 0 && (
                <p className="mt-3 text-[10px] text-muted-foreground">
                  waits for: <span className="font-mono text-cyan-300/80">{node.dependsOn.join(', ')}</span>
                </p>
              )}

              {node.retryReason && (
                <p className="mt-2 rounded border border-rose-400/30 bg-rose-400/5 px-2 py-1 font-mono text-[10px] text-rose-300">
                  retry: {node.retryReason}
                </p>
              )}

              {node.tools.length > 0 && (
                <div className="mt-3">
                  <p className="hud-label">Tool invocations</p>
                  <div className="mt-1 max-h-28 space-y-1 overflow-y-auto">
                    {node.tools.map((t, i) => (
                      <div key={i} className="rounded border border-amber-400/20 bg-amber-400/5 px-2 py-1">
                        <span className="font-mono text-[10px] text-amber-200">{t.name}</span>
                        <p className="truncate font-mono text-[9px] text-muted-foreground">
                          {JSON.stringify(t.args)}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {node.feedback && (
                <div className="mt-3">
                  <p className="hud-label">Reviewer</p>
                  <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{node.feedback}</p>
                </div>
              )}
              {node.error && (
                <p className="mt-2 rounded border border-rose-400/30 bg-rose-400/5 px-2 py-1 text-[10px] text-rose-300">
                  {node.error}
                </p>
              )}

              <div className="mt-3 min-h-0 flex-1">
                <p className="hud-label flex items-center gap-1.5">
                  <MessageSquareText className="h-3 w-3" /> Output stream
                </p>
                <div className="mt-1 h-[calc(100%-20px)] overflow-y-auto rounded-md border border-border/60 bg-background/60 p-2.5">
                  {node.output ? (
                    <TextType
                      key={node.taskId + node.output.length}
                      text={node.output}
                      loop={false}
                      typingSpeed={8}
                      showCursor={false}
                      className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-emerald-100/90"
                    />
                  ) : (
                    <p className="font-mono text-[10px] text-muted-foreground/60">
                      {node.status === 'awaiting-input'
                        ? 'held for operator input…'
                        : node.status === 'queued'
                        ? 'queued — awaiting wave slot…'
                        : 'no output yet'}
                    </p>
                  )}
                </div>
              </div>
            </div>
          </SpotlightCard>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
