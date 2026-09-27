import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useWebSocket } from '@/services/ws';
import { ApiService } from '@/services/api';
import { useToast } from '@/hooks/use-toast';
import { useOrchestration } from '@/console/useOrchestration';
import { PipelineCanvas } from '@/console/PipelineCanvas';
import { TopHud } from '@/console/TopHud';
import { Wordmark, StatusPanel, NotesPanel } from '@/console/StatusRail';
import { ModelsPanel, ProviderStatusPanel, NetworkPanel } from '@/console/ModelsPanel';
import { TelemetryBar } from '@/console/TelemetryBar';
import { KeyRing, loadRing, displayTag, detectProvider, RING_LS, type StoredKey } from '@/console/KeyRing';
import { EventFeed } from '@/console/EventFeed';
import { InspectorDrawer } from '@/console/InspectorDrawer';
import { FinalModal } from '@/console/FinalModal';

const API_KEY_LS = 'orchestrator_api_key';
const SLOT_MODELS_LS = 'orchestrator_slot_models';
const REVIEW_PLAN_LS = 'orchestrator_review_plan';

function readSlots(): Record<string, string> {
  try {
    const parsed = JSON.parse(localStorage.getItem(SLOT_MODELS_LS) ?? '{}');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, string>;
    }
  } catch { /* corrupted storage -> clean */ }
  return {};
}

export default function App() {
  const [goal, setGoal] = useState('');
  const [apiKey, setApiKey] = useState<string>(() => localStorage.getItem(API_KEY_LS) ?? '');
  const [models, setModels] = useState<string[]>([]);
  const [modelProvider, setModelProvider] = useState<string | null>(null);
  const [bayError, setBayError] = useState<string | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [selectedModel, setSelectedModel] = useState('auto');
  const [reviewPlan, setReviewPlan] = useState<boolean>(
    () => localStorage.getItem(REVIEW_PLAN_LS) === '1'
  );
  const [slotModels, setSlotModels] = useState<Record<string, string>>(readSlots);
  const [planAssignments, setPlanAssignments] = useState<Record<string, string>>({});
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [finalOpen, setFinalOpen] = useState(false);
  const [, setTick] = useState(0);
  const { toast } = useToast();

  const { state, log, ingest } = useOrchestration();

  // ---- ticker for live elapsed time while a run is active ----------------
  const active = state.phase === 'planning' || state.phase === 'running' || state.phase === 'awaiting-review';
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setTick(t => t + 1), 500);
    return () => clearInterval(id);
  }, [active]);

  // ---- websocket ----------------------------------------------------------
  const lastRunIdRef = useRef<string | null>(null);
  const { sendMessage, sendFeedback, sendPlanAssignments, sendStop, isConnected } = useWebSocket({
    onEvent: (frame: { type: string; data?: Record<string, unknown> }) => {
      ingest(frame as never);
      if (frame.type === 'execution_started') {
        setPlanAssignments({});
        setSelectedTaskId(null);
        setFinalOpen(false);
      }
      if (frame.type === 'plan_review_requested') {
        // Seed review assignments from the pre-flight slot board
        const tasks = (frame.data?.tasks ?? []) as { task_id: string }[];
        setPlanAssignments(
          Object.fromEntries(tasks.map(t => [t.task_id, slotModels[t.task_id] || 'auto']))
        );
      }
      if (frame.type === 'run_completed') {
        setFinalOpen(true);
        toast({ title: 'Final deliverable ready', description: 'Opening output bay.' });
      }
      if (frame.type === 'execution_failed' || frame.type === 'planning_failed') {
        toast({
          title: 'Run failed',
          description: String((frame.data as Record<string, unknown>)?.error ?? 'pipeline halted'),
          variant: 'destructive'
        });
      }
    },
    onError: (err: string) => {
      toast({ title: 'Link fault', description: err, variant: 'destructive' });
    }
  });

  // ---- models --------------------------------------------------------------
  // Key ring state lives here (above fetchModels) because the bay merges
  // catalogues across every stored key.
  const [ring, setRing] = useState<StoredKey[]>(loadRing);
  const persistRing = useCallback((next: StoredKey[]) => {
    setRing(next);
    localStorage.setItem(RING_LS, JSON.stringify(next));
  }, []);

  // The key set the current bay contents were fetched with (null = nothing
  // fetched yet). Divergence from it invalidates the bay + slot assignments.
  const fetchedKeyRef = useRef<string | null>(null);
  // Last key-set the "planner model unavailable" warning was shown for.
  const plannerWarnRef = useRef<string>('');
  // model id -> API key that served it, and -> display tag of that account
  const [modelKeyMap, setModelKeyMap] = useState<Record<string, string>>({});
  const [modelTags, setModelTags] = useState<Record<string, string>>({});

  const tagForKey = useCallback((key: string): string => {
    const entry = ring.find(e => e.key === key);
    if (entry) return displayTag(entry);
    return key ? detectProvider(key) : 'SERVER';
  }, [ring]);

  const clearBay = useCallback(() => {
    setModels([]);
    setModelProvider(null);
    setModelKeyMap({});
    setModelTags({});
    fetchedKeyRef.current = null;
  }, []);

  // One bay across MANY keys: fetch the catalogue for every stored key
  // (plus the active one if it is not in the ring), merge deduplicated by
  // model id, and remember which key serves each model so the run can route
  // per-task calls to the right account.
  const fetchModels = useCallback(async (silent = false, keyOverride?: string) => {
    setLoadingModels(true);
    const active = apiKey.trim();
    let jobs: { key: string; tag: string }[];
    if (keyOverride !== undefined) {
      const k = keyOverride.trim();
      jobs = [{ key: k, tag: tagForKey(k) }];
    } else if (ring.length > 0) {
      jobs = ring.map(e => ({ key: e.key, tag: displayTag(e) }));
      if (active && !ring.some(e => e.key === active)) {
        jobs.push({ key: active, tag: tagForKey(active) });
      }
    } else {
      jobs = [{ key: active, tag: tagForKey(active) }];
    }

    const settled = await Promise.allSettled(
      jobs.map(j => ApiService.getModels(j.key || undefined))
    );

    const nextModels: string[] = [];
    const nextKeyMap: Record<string, string> = {};
    const nextTags: Record<string, string> = {};
    let okCount = 0;
    settled.forEach((res, i) => {
      if (res.status !== 'fulfilled') return;
      okCount += 1;
      for (const m of res.value.models) {
        if (nextTags[m]) continue;  // first key serving a model wins
        nextModels.push(m);
        nextKeyMap[m] = jobs[i].key;
        nextTags[m] = jobs[i].tag === 'SERVER' ? res.value.provider : jobs[i].tag;
      }
    });

    if (okCount === 0) {
      clearBay();
      setBayError('INVALID OR MISSING KEY');
      if (!silent) {
        toast({
          title: 'Model fetch failed',
          description: 'No stored key returned a model list.',
          variant: 'destructive'
        });
      }
      setLoadingModels(false);
      return;
    }

    setModels(nextModels);
    setModelKeyMap(nextKeyMap);
    setModelTags(nextTags);
    setModelProvider(okCount === 1 ? (Object.values(nextTags)[0] ?? null) : `${okCount} KEYS`);
    setBayError(null);
    fetchedKeyRef.current = active;
    // If no stored key can serve the server's planner model, the run will
    // die at planning unless a specific model is forced (that choice now
    // drives the planner call too). Warn up front, once per key set.
    const defaultModel =
      settled.find(r => r.status === 'fulfilled')?.value.default_model ?? '';
    const warnKey = `${defaultModel}|${jobs.map(j => j.key).join(',')}`;
    if (
      defaultModel &&
      nextModels.length > 0 &&
      !nextModels.includes(defaultModel) &&
      plannerWarnRef.current !== warnKey
    ) {
      plannerWarnRef.current = warnKey;
      toast({
        title: 'Planner model unavailable',
        description: `Your keys cannot serve ${defaultModel}, the planner's default. Pick a model in the strategy dropdown (it routes the planner too) or add a key with access.`,
        variant: 'destructive'
      });
    }
    if (!silent) {
      toast({
        title: 'Models online',
        description: `${nextModels.length} models from ${okCount} key${okCount === 1 ? '' : 's'}.`,
      });
    }
    setLoadingModels(false);
  }, [apiKey, ring, toast, tagForKey, clearBay]);

  // Load the server-key catalogue once at boot so the bay is never empty.
  useEffect(() => {
    fetchModels(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- persistence ---------------------------------------------------------
  const handleApiKeyChange = (v: string) => {
    setApiKey(v);
    if (v) localStorage.setItem(API_KEY_LS, v);
    else localStorage.removeItem(API_KEY_LS);
    // Stale-cache guard: once the key differs from the one the bay was
    // fetched with, drop the cached list AND any slot pre-assignments made
    // with it — they would only fail at execution time.
    if (fetchedKeyRef.current !== null && v.trim() !== fetchedKeyRef.current) {
      clearBay();
      handleClearSlots();
      setBayError(null);
    }
  };
  const handleReviewPlanToggle = (v: boolean) => {
    setReviewPlan(v);
    if (v) localStorage.setItem(REVIEW_PLAN_LS, '1');
    else localStorage.removeItem(REVIEW_PLAN_LS);
  };
  const handleAssignSlot = useCallback((slot: string, model: string) => {
    setSlotModels(prev => {
      const next = { ...prev, [slot]: model };
      localStorage.setItem(SLOT_MODELS_LS, JSON.stringify(next));
      return next;
    });
  }, []);

  const handleClearSlots = useCallback(() => {
    setSlotModels({});
    localStorage.removeItem(SLOT_MODELS_LS);
  }, []);

  // ---- key ring actions ------------------------------------------------------
  const handleAddRingKey = (entry: StoredKey) => persistRing([entry, ...ring]);
  const handleUseRingKey = (k: string) => {
    setApiKey(k);
    localStorage.setItem(API_KEY_LS, k);
    // Slots were picked under whatever key was active before — wipe them.
    handleClearSlots();
    fetchModels(false, k);
  };
  const handleRemoveRingEntry = (entry: StoredKey) => {
    persistRing(ring.filter(e => e.id !== entry.id));
    if (entry.key === apiKey) handleApiKeyChange('');
  };

  // ---- run control ---------------------------------------------------------
  const handleSubmit = () => {
    if (!goal.trim() || !isConnected || active) return;
    const preselected = Object.fromEntries(
      Object.entries(slotModels).filter(([, m]) => m && m !== 'auto')
    );
    const chosen = new Set<string>(Object.values(preselected));
    if (selectedModel !== 'auto') chosen.add(selectedModel);
    // Route each chosen model's calls to the exact key the bay fetched it
    // with — different tasks can run on different providers in one wave.
    const modelKeys: Record<string, string> = {};
    for (const m of chosen) {
      const k = modelKeyMap[m];
      if (k) modelKeys[m] = k;
    }
    sendMessage(
      goal.trim(),
      apiKey.trim() || undefined,
      selectedModel !== 'auto' ? selectedModel : undefined,
      reviewPlan || undefined,
      Object.keys(preselected).length > 0 ? preselected : undefined,
      Object.keys(modelKeys).length > 0 ? modelKeys : undefined,
      // Fetched bay catalogue: constrains the planner's picks and arms the
      // backend's pre-wave validation guardrail.
      models.length > 0 ? models : undefined
    );
  };

  const handleAssignTask = useCallback((taskId: string, model: string) => {
    setPlanAssignments(prev => ({ ...prev, [taskId]: model }));
  }, []);

  const handleFeedbackSubmit = useCallback((taskId: string, text: string) => {
    sendFeedback(taskId, text);
  }, [sendFeedback]);

  const handleStartReview = () => {
    const tasks = state.reviewTasks ?? [];
    const payload: Record<string, string> = {};
    for (const t of tasks) {
      payload[t.task_id] = planAssignments[t.task_id] ?? 'auto';
    }
    sendPlanAssignments(payload);
  };

  const selectedNode = selectedTaskId ? state.nodes[selectedTaskId] : null;
  const elapsedMs = state.startedAt
    ? (state.finishedAt ?? Date.now()) - state.startedAt
    : 0;
  const assignedCount = Object.values(planAssignments).filter(v => v && v !== 'auto').length;

  return (
    <div className="relative flex h-screen flex-col overflow-hidden">
      <div className="crt-film" aria-hidden="true" />

      <div className="flex min-h-0 flex-1 gap-3 p-3 pb-0">
        {/* Left rail: identity, live status, event log, notes */}
        <aside className="flex w-[280px] shrink-0 flex-col gap-3 min-h-0 overflow-y-auto pr-0.5">
          <Wordmark />
          <StatusPanel state={state} elapsedMs={elapsedMs} />
          <EventFeed log={log} state={state} isConnected={isConnected} elapsedMs={elapsedMs} />
          <NotesPanel state={state} />
        </aside>

        {/* Center: HUD controls + DAG canvas */}
        <section className="flex min-w-0 flex-1 flex-col gap-3">
          <TopHud
            goal={goal}
            setGoal={setGoal}
            models={models}
            selectedModel={selectedModel}
            setSelectedModel={setSelectedModel}
            reviewPlan={reviewPlan}
            setReviewPlan={handleReviewPlanToggle}
            onSubmit={handleSubmit}
            onStop={sendStop}
            isConnected={isConnected}
            phase={state.phase}
            tokens={state.totalTokens}
            reviewReady={state.phase === 'awaiting-review'}
            onStartReview={handleStartReview}
            assignedCount={assignedCount}
          />
          <div className="rail-panel relative min-h-0 flex-1 overflow-hidden">
            <PipelineCanvas
              state={state}
              models={models}
              slotModels={slotModels}
              assignmentOverride={state.phase === 'awaiting-review' ? planAssignments : undefined}
              onAssignTask={handleAssignTask}
              onAssignSlot={handleAssignSlot}
              onClearSlots={handleClearSlots}
              onFeedback={handleFeedbackSubmit}
              onInspect={setSelectedTaskId}
              onPaneClick={() => setSelectedTaskId(null)}
              selectedTaskId={selectedTaskId}
            />
            <InspectorDrawer node={selectedNode ?? null} onClose={() => setSelectedTaskId(null)} />
          </div>
        </section>

        {/* Right rail: model catalogue, provider + network state */}
        <aside className="flex w-[290px] shrink-0 flex-col gap-3 min-h-0">
          <ModelsPanel
            models={models}
            modelTags={modelTags}
            providerTag={modelProvider}
            loading={loadingModels}
            bayError={bayError}
            onRefresh={() => fetchModels(false)}
          />
          <ProviderStatusPanel modelTags={modelTags} bayError={bayError} />
          <NetworkPanel
            isConnected={isConnected}
            modelCount={models.length}
            eventCount={state.eventCount}
          />
        </aside>
      </div>

      <TelemetryBar state={state} />

      <KeyRing
        keys={ring}
        activeKey={apiKey}
        loading={loadingModels}
        onAddKey={handleAddRingKey}
        onUseKey={handleUseRingKey}
        onRemoveKey={handleRemoveRingEntry}
      />

      <FinalModal
        output={state.finalOutput ?? ''}
        open={finalOpen}
        onClose={() => setFinalOpen(false)}
      />
    </div>
  );
}
