import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { Check, Copy, X } from 'lucide-react';
import BorderGlow from '@/components/reactbits/BorderGlow';
import DecryptedText from '@/components/reactbits/DecryptedText';

export function FinalModal({
  output, open, onClose
}: { output: string; open: boolean; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => { if (!open) setCopied(false); }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-6 backdrop-blur-sm"
          onClick={onClose}
        >
          <motion.div
            initial={{ scale: 0.94, y: 14 }}
            animate={{ scale: 1, y: 0 }}
            exit={{ scale: 0.94, y: 14, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 260, damping: 24 }}
            onClick={e => e.stopPropagation()}
            className="max-h-[85vh] w-full max-w-3xl"
          >
            <BorderGlow animated borderRadius={18} glowRadius={30} colors={['#00f0ff', '#00ff66', '#0ea5e9']}>
              <div className="flex flex-col max-h-[85vh]">
                <div className="flex items-center justify-between px-5 pt-4">
                  <DecryptedText
                    text="FINAL DELIVERABLE"
                    animateOn="view"
                    sequential
                    speed={26}
                    revealDirection="start"
                    className="text-sm font-bold tracking-[0.3em] neon-text"
                    encryptedClassName="text-cyan-400/30"
                  />
                  <button onClick={onClose} className="rounded p-1 text-muted-foreground hover:text-foreground">
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <div className="m-4 mt-3 overflow-y-auto rounded-lg border border-border/70 bg-background/70 p-4">
                  {output ? (
                    <pre className="whitespace-pre-wrap break-words font-mono text-[12.5px] leading-relaxed text-foreground/90">
                      {output}
                    </pre>
                  ) : (
                    <p className="font-mono text-[12.5px] leading-relaxed text-muted-foreground">
                      No deliverable — the run finished without a usable output
                      (tasks were rejected or failed). Open a task node in the
                      canvas for its error, or re-engage with a different model.
                    </p>
                  )}
                </div>
                <div className="flex justify-end gap-2 px-5 pb-4">
                  <button
                    disabled={!output}
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(output);
                        setCopied(true);
                        setTimeout(() => setCopied(false), 1500);
                      } catch { /* clipboard unavailable */ }
                    }}
                    className="flex items-center gap-2 rounded-lg bg-cyan-400 px-4 py-2 text-xs font-bold text-black hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                    {copied ? 'COPIED' : 'COPY OUTPUT'}
                  </button>
                </div>
              </div>
            </BorderGlow>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
