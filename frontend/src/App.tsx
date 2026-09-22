import React, { useState, useEffect, memo, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { useWebSocket, PlanReviewTask } from '@/services/ws';
import { ApiService } from '@/services/api';
import { RunData } from '@/types/api';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Sidebar, SidebarContent, SidebarTrigger } from '@/components/ui/sidebar';
import { ChevronLeft, ChevronRight, Play, RefreshCw } from 'lucide-react';
import { useToast, ToastProvider } from '@/hooks/use-toast';

interface TaskSummary {
  goal?: string;
  total_tasks?: number;
  completed_tasks?: number;
  rejected_tasks?: number;
  status?: string;
}

interface TaskDetail {
  task_id?: string;
  description?: string;
  status?: string;
  attempts?: number;
  output?: string;
}

interface ExecutionLogEntry {
  type: string;
  data: Record<string, unknown>;
}

interface PendingInput {
  taskId: string;
  taskIndex: number;
  question: string;
}

const formatLogEntry = (entry: ExecutionLogEntry) => {
  switch (entry.type) {
    case 'task_started':
      return {
        type: 'info',
        content: `[TASK ${entry.data.taskIndex}] Starting: ${entry.data.description}`
      };
    case 'task_worker_completed':
      return {
        type: 'success',
        content: `[TASK ${entry.data.taskIndex}] Completed (attempt ${entry.data.attempts}): ${(entry.data.output as string).substring(0, 100)}${(entry.data.output as string).length > 100 ? '...' : ''}`
      };
    case 'task_review_passed':
      return {
        type: 'success',
        content: `[TASK ${entry.data.taskIndex}] Review PASSED: ${entry.data.feedback}`
      };
    case 'task_review_failed':
      return {
        type: 'error',
        content: `[TASK ${entry.data.taskIndex}] Review FAILED: ${entry.data.feedback}`
      };
    case 'tool_execution':
      return {
        type: 'tool',
        content: `[TOOL${entry.data.taskId ? ` ${entry.data.taskId}` : ''}] ${entry.data.toolName} running with ${JSON.stringify(entry.data.args)}`
      };
    case 'task_retry':
      return {
        type: 'retry',
        content: `[RETRY] Task ${entry.data.taskId} -> attempt ${entry.data.attempt}: ${entry.data.reason}`
      };
    case 'task_requires_input':
      return {
        type: 'input_request',
        content: `[INPUT] Task ${entry.data.taskId} paused, waiting for you: ${entry.data.question}`
      };
    case 'task_resumed':
      return {
        type: 'success',
        content: `[RESUME] Task ${entry.data.taskId} continuing with your feedback`
      };
    case 'task_input_timeout':
      return {
        type: 'error',
        content: `[TIMEOUT] Task ${entry.data.taskId} failed: ${entry.data.reason}`
      };
    case 'model_selection':
      return {
        type: 'info',
        content: `[MODEL] All ${entry.data.taskCount} tasks forced onto ${entry.data.model} (user selection)`
      };
    case 'plan_review_requested':
      return {
        type: 'info',
        content: `[PLAN] Paused after planning — assign models per task to start execution`
      };
    case 'plan_review_completed':
      return {
        type: 'success',
        content: `[PLAN] Assignments applied (${entry.data.assigned} task(s) got a custom model); execution started`
      };
    case 'plan_review_timeout':
      return {
        type: 'error',
        content: `[PLAN] Review window closed: ${entry.data.reason}`
      };
    case 'plan_models_applied':
      return {
        type: 'success',
        content: `[PLAN] Pre-assigned models applied to ${entry.data.assigned} task slot(s)`
      };
    default:
      return {
        type: 'log',
        content: JSON.stringify(entry.data)
      };
  }
};

// Defined at module scope so its identity is stable across App renders —
// memo() actually skips re-renders when only finalOutput changes.
const ExecutionLogStream = memo(({ logs, isExecuting }: { logs: ExecutionLogEntry[]; isExecuting: boolean }) => (
  <ScrollArea className="h-[400px] w-full bg-muted/50 rounded p-4 space-y-2">
    {logs.length === 0 && !isExecuting ? (
      <p className="text-muted-foreground text-center py-8">
        No execution logs yet. Submit a goal to see live updates.
      </p>
    ) : (
      <>
        {logs.map((entry, index) => {
          const formatted = formatLogEntry(entry);
          return (
            <div key={index} className={`px-3 py-2 rounded-lg border-l-4 ${
              formatted.type === 'error' ? 'border-destructive bg-destructive/10' :
              formatted.type === 'success' ? 'border-success bg-success/10' :
              formatted.type === 'info' ? 'border-primary bg-primary/10' :
              formatted.type === 'tool' ? 'border-warning bg-warning/10' :
              formatted.type === 'retry' ? 'border-warning bg-warning/10 animate-pulse' :
              formatted.type === 'input_request' ? 'border-warning bg-warning/15 animate-pulse' :
              'border-muted bg-muted/5'
            }`}>
              <div className="flex items-start gap-2">
                <div className={`flex-shrink-0 h-2.5 w-2.5 rounded-full ${
                  formatted.type === 'error' ? 'bg-destructive' :
                  formatted.type === 'success' ? 'bg-success' :
                  formatted.type === 'info' ? 'bg-primary' :
                  formatted.type === 'tool' ? 'bg-warning' :
                  formatted.type === 'retry' ? 'bg-warning' :
                  formatted.type === 'input_request' ? 'bg-warning' :
                  'bg-muted'
                }`}></div>
                <div className="text-sm whitespace-pre-wrap font-mono">{formatted.content}</div>
              </div>
            </div>
          );
        })}
      </>
    )}
  </ScrollArea>
));

// Module scope for the same reason: re-renders only when output/isExecuting change.
const FinalResultPanel = memo(({ output, isExecuting }: { output: string | null; isExecuting: boolean }) => {
  return (
    <Card className="border-2 border-primary/40 bg-gradient-to-b from-primary/5 to-background">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <div className={`h-2.5 w-2.5 rounded-full ${isExecuting ? 'bg-warning animate-pulse' : 'bg-success'}`} />
          <CardTitle className="text-lg font-semibold text-primary">Final Result</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        {output && output.trim() ? (
          <div className="bg-background/80 rounded-lg p-4 border border-border">
            <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground">
              {output}
            </p>
          </div>
        ) : (
          <div className="bg-background/50 rounded-lg p-4 border border-dashed border-border">
            <p className="text-sm text-muted-foreground text-center py-2">
              {isExecuting
                ? 'Working on the final result...'
                : 'No final output yet. Submit a goal to see the final result here.'}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
});

// One paused task: question + free-text field + Approve / Provide Feedback.
function PendingInputCard({ request, onSubmit }: {
  request: PendingInput;
  onSubmit: (taskId: string, feedback: string) => void;
}) {
  const [text, setText] = useState('');
  const [answered, setAnswered] = useState(false);

  const submit = (feedback: string) => {
    if (answered) return;
    onSubmit(request.taskId, feedback);
    setAnswered(true);
  };

  return (
    <div className="rounded-lg border border-warning/40 bg-background/70 p-4 space-y-3">
      <div className="text-sm font-medium">Task {request.taskId}</div>
      <p className="text-sm text-muted-foreground whitespace-pre-wrap">{request.question}</p>
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Add direction for the agent (optional)…"
        className="min-h-[60px]"
        disabled={answered}
      />
      <div className="flex justify-end gap-3">
        <Button
          variant="outline"
          size="sm"
          disabled={answered}
          onClick={() => submit('APPROVED')}
        >
          {answered ? 'Submitted' : 'Approve'}
        </Button>
        <Button
          variant="default"
          size="sm"
          disabled={answered || !text.trim()}
          onClick={() => submit(text.trim())}
        >
          {answered ? 'Submitted' : 'Provide Feedback'}
        </Button>
      </div>
    </div>
  );
}

// Rendered whenever at least one task is in REQUIRES_USER_INPUT.
function UserInputRequestsPanel({ requests, onSubmit }: {
  requests: PendingInput[];
  onSubmit: (taskId: string, feedback: string) => void;
}) {
  if (requests.length === 0) return null;
  return (
    <Card className="border-2 border-warning/60 bg-warning/5">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-warning animate-pulse" />
          <CardTitle className="text-lg font-semibold">
            Your Input Needed ({requests.length})
          </CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Execution is paused for these tasks — the pipeline resumes automatically
          once you respond. Everything else keeps running.
        </p>
        {requests.map((r) => (
          <PendingInputCard key={r.taskId} request={r} onSubmit={onSubmit} />
        ))}
      </CardContent>
    </Card>
  );
}

// Shown between planning and execution when "assign per task" is checked:
// one row per planned task, each with its own model dropdown.
function PlanReviewPanel({ tasks, models, assignments, sent, onAssign, onStart }: {
  tasks: PlanReviewTask[];
  models: string[];
  assignments: Record<string, string>;
  sent: boolean;
  onAssign: (taskId: string, model: string) => void;
  onStart: () => void;
}) {
  return (
    <Card className="border-2 border-primary/50 bg-primary/5">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-primary animate-pulse" />
          <CardTitle className="text-lg font-semibold">Review Plan — Pick a Model per Task</CardTitle>
        </div>
        <p className="text-sm text-muted-foreground">
          Execution is paused. Each row shows what the task will do, what it
          waits for, and the model the Planner proposed. Override any of them,
          then start — tasks still run concurrently wherever their dependencies allow.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {tasks.map((t) => {
          const fallbackLabel = t.planner_model === 'auto' ? 'planner default' : t.planner_model;
          return (
            <div
              key={t.task_id}
              className="rounded-lg border border-border bg-background/70 p-3 flex flex-col md:flex-row md:items-center gap-3"
            >
              <div className="font-mono text-xs text-muted-foreground w-8 shrink-0">{t.task_id}</div>
              <div className="flex-1 min-w-0">
                <div className="text-sm">{t.description}</div>
                <div className="text-[11px] text-muted-foreground">
                  {t.depends_on.length > 0
                    ? `waits for ${t.depends_on.join(', ')}`
                    : 'starts immediately (no dependencies)'}
                  {t.planner_model !== 'auto' && ` · planner picked ${t.planner_model}`}
                  {t.requires_user_input && ' · asks your approval before starting'}
                </div>
              </div>
              <select
                aria-label={`Model for ${t.task_id}`}
                value={assignments[t.task_id] ?? 'auto'}
                onChange={(e) => onAssign(t.task_id, e.target.value)}
                disabled={sent}
                className="h-9 w-full md:w-72 shrink-0 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
              >
                <option value="auto">Auto ({fallbackLabel})</option>
                {models.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
          );
        })}
        <div className="flex justify-end pt-1">
          <Button onClick={onStart} disabled={sent} className="px-5">
            {sent ? 'Starting…' : 'Start Execution'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// Pre-run per-slot model choices (Planner always ids tasks t1..t7).
const SLOT_IDS = ['t1', 't2', 't3', 't4', 't5', 't6', 't7'];
const SLOT_STORAGE_KEY = 'orchestrator_slot_models';

function loadSlotModels(): Record<string, string> {
  try {
    const parsed = JSON.parse(localStorage.getItem(SLOT_STORAGE_KEY) ?? '{}');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, string>;
    }
  } catch {
    /* corrupted storage -> start clean */
  }
  return {};
}

export default function App() {
  const [inputValue, setInputValue] = useState('');
  const [isExecuting, setIsExecuting] = useState(false);
  const [runs, setRuns] = useState<RunData[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [executionLogs, setExecutionLogs] = useState<ExecutionLogEntry[]>([]);
  const [finalOutput, setFinalOutput] = useState<string | null>(null);
  const [pendingInputs, setPendingInputs] = useState<PendingInput[]>([]);
  // Per-run provider key. Kept out of React's rendered text (masked input);
  // persisted so a page reload doesn't force re-entering it.
  const [apiKey, setApiKey] = useState<string>(
    () => localStorage.getItem('orchestrator_api_key') ?? ''
  );
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>('auto');
  const [loadingModels, setLoadingModels] = useState(false);
  const [reviewPlan, setReviewPlan] = useState<boolean>(
    () => localStorage.getItem('orchestrator_review_plan') === '1'
  );
  const [planReviewTasks, setPlanReviewTasks] = useState<PlanReviewTask[] | null>(null);
  const [planAssignments, setPlanAssignments] = useState<Record<string, string>>({});
  const [planReviewSent, setPlanReviewSent] = useState(false);
  const [slotModels, setSlotModels] = useState<Record<string, string>>(loadSlotModels);
  const { toast } = useToast();

  const handleSlotModelChange = (slot: string, model: string) => {
    setSlotModels(prev => {
      const next = { ...prev, [slot]: model };
      localStorage.setItem(SLOT_STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  };

  const handleClearSlots = () => {
    setSlotModels({});
    localStorage.removeItem(SLOT_STORAGE_KEY);
  };

  const fetchModels = useCallback(async (opts?: { silent?: boolean }) => {
    setLoadingModels(true);
    try {
      const list = await ApiService.getModels(apiKey.trim() || undefined);
      setModels(list);
      if (!opts?.silent) {
        toast({
          title: 'Models loaded',
          description: `${list.length} models available${apiKey.trim() ? ' for your key' : ' on the server key'}.`,
        });
      }
    } catch (error: unknown) {
      setModels([]);
      if (!opts?.silent) {
        toast({
          title: 'Model fetch failed',
          description: error instanceof Error ? error.message : 'Could not list models',
          variant: 'destructive',
        });
      }
    } finally {
      setLoadingModels(false);
    }
  }, [apiKey, toast]);

  // Populate the picker with the server-key catalogue on first load; the
  // "Fetch Models" button re-queries with a custom key afterwards.
  useEffect(() => {
    fetchModels({ silent: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleApiKeyChange = (value: string) => {
    setApiKey(value);
    if (value) {
      localStorage.setItem('orchestrator_api_key', value);
    } else {
      localStorage.removeItem('orchestrator_api_key');
    }
  };

  const { sendMessage, sendFeedback, sendPlanAssignments, isConnected } = useWebSocket({
    onExecutionStart: (goal: string) => {
      setIsExecuting(true);
      setFinalOutput(null);
      setExecutionLogs([]);
      setPendingInputs([]);
      setPlanReviewTasks(null);
      setPlanReviewSent(false);
      toast({
        title: "Execution Started",
        description: `Started execution for: "${goal}"`,
      });
    },
    onExecutionComplete: (summary: TaskSummary, tasks: TaskDetail[]) => {
      setIsExecuting(false);
      setPendingInputs([]);
      // Refresh runs list
      fetchRuns();
      toast({
        title: "Execution Complete",
        description: `Completed with ${summary.completed_tasks ?? 0} successful tasks`,
      });
    },
    onRunComplete: (finalOutput: string) => {
      setFinalOutput(finalOutput);
    },
    onTaskStart: (taskIndex: number, taskId: string, description: string) => {
      setExecutionLogs(prev => [
        ...prev,
        {
          type: 'task_started',
          data: { taskIndex, taskId, description }
        }
      ]);
    },
    onTaskWorkerComplete: (taskIndex: number, taskId: string, output: string, attempts: number) => {
      setExecutionLogs(prev => [
        ...prev,
        {
          type: 'task_worker_completed',
          data: { taskIndex, taskId, output, attempts }
        }
      ]);
    },
    onTaskReviewPassed: (taskIndex: number, taskId: string, feedback: string) => {
      setExecutionLogs(prev => [
        ...prev,
        {
          type: 'task_review_passed',
          data: { taskIndex, taskId, feedback }
        }
      ]);
    },
    onTaskReviewFailed: (taskIndex: number, taskId: string, feedback: string) => {
      setExecutionLogs(prev => [
        ...prev,
        {
          type: 'task_review_failed',
          data: { taskIndex, taskId, feedback }
        }
      ]);
    },
    onToolExecute: (toolName: string, args: Record<string, unknown>, taskId?: string) => {
      setExecutionLogs(prev => [
        ...prev,
        {
          type: 'tool_execution',
          data: { toolName, args, taskId }
        }
      ]);
    },
    onTaskRetry: (taskId: string, attempt: number, reason: string) => {
      setExecutionLogs(prev => [
        ...prev,
        {
          type: 'task_retry',
          data: { taskId, attempt, reason }
        }
      ]);
    },
    onTaskRequiresInput: (taskId: string, taskIndex: number, question: string) => {
      setPendingInputs(prev =>
        prev.some(p => p.taskId === taskId)
          ? prev
          : [...prev, { taskId, taskIndex, question }]
      );
      setExecutionLogs(prev => [
        ...prev,
        { type: 'task_requires_input', data: { taskId, taskIndex, question } }
      ]);
      toast({
        title: 'Your input needed',
        description: `Task ${taskId} is paused until you respond.`,
      });
    },
    onTaskResumed: (taskId: string, feedback: string) => {
      setPendingInputs(prev => prev.filter(p => p.taskId !== taskId));
      setExecutionLogs(prev => [
        ...prev,
        { type: 'task_resumed', data: { taskId, feedback } }
      ]);
    },
    onTaskInputTimeout: (taskId: string, reason: string) => {
      setPendingInputs(prev => prev.filter(p => p.taskId !== taskId));
      setExecutionLogs(prev => [
        ...prev,
        { type: 'task_input_timeout', data: { taskId, reason } }
      ]);
    },
    onModelSelection: (model: string, taskCount: number) => {
      setExecutionLogs(prev => [
        ...prev,
        { type: 'model_selection', data: { model, taskCount } }
      ]);
    },
    onPlanReviewRequest: (tasks: PlanReviewTask[]) => {
      setPlanReviewTasks(tasks);
      setPlanReviewSent(false);
      // Pre-fill rows with any pre-assigned slot models, else the Planner's pick
      setPlanAssignments(
        Object.fromEntries(tasks.map(t => [
          t.task_id,
          slotModels[t.task_id] && slotModels[t.task_id] !== 'auto'
            ? slotModels[t.task_id]
            : 'auto'
        ]))
      );
      setExecutionLogs(prev => [
        ...prev,
        { type: 'plan_review_requested', data: { taskCount: tasks.length } }
      ]);
      toast({
        title: 'Plan ready for review',
        description: 'Pick a model per task, then start execution.',
      });
    },
    onPlanReviewCompleted: (assigned: number) => {
      setPlanReviewTasks(null);
      setPlanReviewSent(false);
      setExecutionLogs(prev => [
        ...prev,
        { type: 'plan_review_completed', data: { assigned } }
      ]);
    },
    onPlanReviewTimeout: (reason: string) => {
      setPlanReviewTasks(null);
      setPlanReviewSent(false);
      setExecutionLogs(prev => [
        ...prev,
        { type: 'plan_review_timeout', data: { reason } }
      ]);
    },
    onPlanModelsApplied: (assigned: number) => {
      setExecutionLogs(prev => [
        ...prev,
        { type: 'plan_models_applied', data: { assigned } }
      ]);
    },
    onError: (error: string) => {
      setIsExecuting(false);
      toast({
        title: "Error",
        description: error,
        variant: "destructive"
      });
    },
    onConnectionChange: (isConnected: boolean) => {
      if (!isConnected) {
        toast({
          title: "Connection Lost",
          description: "WebSocket connection lost. Attempting to reconnect...",
          variant: "destructive"
        });
      }
    }
  });

  useEffect(() => {
    fetchRuns();
  }, []);

  const fetchRuns = async () => {
    try {
      const data = await ApiService.getRuns();
      setRuns(data);
    } catch (error: unknown) {
      console.error('Failed to fetch runs:', error);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputValue.trim() || isExecuting) return;

    try {
      setIsExecuting(true);
      setExecutionLogs([]);
      const result = await ApiService.executeGoal(inputValue);
      // The API path is non-streaming, so populate the final output from the
      // last completed task's output (same source the WS path uses).
      const tasks: TaskDetail[] = result?.tasks ?? [];
      const lastCompleted = tasks
        .filter(t => t.status === 'completed' || t.status === 'approved_after_retry')
        .pop();
      setFinalOutput(lastCompleted?.output?.trim() || null);
      setIsExecuting(false);
      // Refresh runs list
      fetchRuns();
      toast({
        title: "Execution Complete",
        description: `Completed with ${result?.summary?.completed_tasks ?? 0} successful tasks`,
      });
      setInputValue('');
    } catch (error: unknown) {
      setIsExecuting(false);
      setFinalOutput(null);
      toast({
        title: "Error",
        description: "Failed to start execution",
        variant: "destructive"
      });
    }
  };

  const handleWebSocketSubmit = () => {
    if (!inputValue.trim() || !isConnected || isExecuting) return;
    const preselected = Object.fromEntries(
      Object.entries(slotModels).filter(([, m]) => m && m !== 'auto')
    );
    sendMessage(
      inputValue,
      apiKey.trim() || undefined,
      selectedModel !== 'auto' ? selectedModel : undefined,
      reviewPlan || undefined,
      Object.keys(preselected).length > 0 ? preselected : undefined
    );
    setIsExecuting(true);
    setExecutionLogs([]);
    setInputValue('');
  };

  const handleReviewPlanToggle = (checked: boolean) => {
    setReviewPlan(checked);
    if (checked) {
      localStorage.setItem('orchestrator_review_plan', '1');
    } else {
      localStorage.removeItem('orchestrator_review_plan');
    }
  };

  const handlePlanAssign = (taskId: string, model: string) => {
    setPlanAssignments(prev => ({ ...prev, [taskId]: model }));
  };

  const handleStartPlanExecution = () => {
    if (!planReviewTasks || planReviewSent) return;
    setPlanReviewSent(true);
    sendPlanAssignments(planAssignments);
  };

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="bg-primary/90 text-primary-foreground px-6 py-4 shadow-sm">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-2xl font-bold">AI Team Orchestrator Dashboard</h1>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 px-3 py-1 rounded bg-primary/20 text-primary-foreground/80 text-sm">
              {isConnected ? '● Connected' : '○ Disconnected'}
            </div>
            <button
              onClick={fetchRuns}
              className="px-3 py-1 rounded border border-primary/30 bg-primary/10 text-primary-foreground/80 hover:bg-primary/20 text-sm"
            >
              Refresh History
            </button>
            {isExecuting && (
              <div className="flex items-center gap-2">
                <span className="text-sm text-primary-foreground">Running</span>
                <Play className="h-4 w-4 animate-pulse text-primary" />
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          {/* Input Panel */}
          <div className="lg:col-span-2">
            <Card className="h-full">
              <CardHeader className="pb-4">
                <CardTitle className="text-lg font-semibold">New Goal</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <form onSubmit={handleSubmit} className="space-y-4">
                  <div>
                    <Textarea
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                      placeholder="Describe what you want the AI team to accomplish..."
                      className="min-h-[80px]"
                      disabled={isExecuting}
                    />
                  </div>
                  <div className="space-y-1">
                    <label
                      htmlFor="provider-api-key"
                      className="text-xs font-medium text-muted-foreground"
                    >
                      Custom OpenAI/Provider API Key (optional — overrides the server's .env key for this run)
                    </label>
                    <Input
                      id="provider-api-key"
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      value={apiKey}
                      onChange={(e) => handleApiKeyChange(e.target.value)}
                      placeholder="sk-... (comma-separated keys rotate per request)"
                      disabled={isExecuting}
                    />
                    {apiKey && (
                      <p className="text-[11px] text-muted-foreground">
                        Stored in this browser's localStorage; sent only with runs you start.
                      </p>
                    )}
                  </div>
                  <div className="space-y-1">
                    <label htmlFor="model-select" className="text-xs font-medium text-muted-foreground">
                      Execution model
                    </label>
                    <div className="flex gap-2">
                      <select
                        id="model-select"
                        value={selectedModel}
                        onChange={(e) => setSelectedModel(e.target.value)}
                        disabled={isExecuting}
                        className="flex h-9 w-full items-center rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
                      >
                        <option value="auto">Auto (Planner Decides)</option>
                        {models.map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => fetchModels()}
                        disabled={loadingModels || isExecuting}
                        className="shrink-0 px-3"
                      >
                        <RefreshCw className={`h-4 w-4 mr-1 ${loadingModels ? 'animate-spin' : ''}`} />
                        {loadingModels ? 'Fetching…' : 'Fetch Models'}
                      </Button>
                    </div>
                    {models.length === 0 && !loadingModels && (
                      <p className="text-[11px] text-muted-foreground">
                        Model list is empty — press Fetch Models (add a custom key above first if needed).
                      </p>
                    )}
                    <label className="flex items-center gap-2 pt-1 text-xs text-muted-foreground cursor-pointer">
                      <input
                        type="checkbox"
                        checked={reviewPlan}
                        onChange={(e) => handleReviewPlanToggle(e.target.checked)}
                        className="h-3.5 w-3.5 accent-primary"
                      />
                      Review the plan first — assign a model per task (pauses after planning)
                    </label>
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-medium text-muted-foreground">
                        Pre-assign models to task slots (applied right after planning — no pause)
                      </label>
                      {Object.values(slotModels).some(m => m && m !== 'auto') && (
                        <button
                          type="button"
                          onClick={handleClearSlots}
                          className="text-[11px] text-muted-foreground underline hover:text-foreground"
                        >
                          Clear all
                        </button>
                      )}
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {SLOT_IDS.map(slot => (
                        <div key={slot} className="flex items-center gap-2">
                          <span className="font-mono text-xs text-muted-foreground w-6 shrink-0">{slot}</span>
                          <select
                            aria-label={`Pre-assigned model for ${slot}`}
                            value={slotModels[slot] ?? 'auto'}
                            onChange={(e) => handleSlotModelChange(slot, e.target.value)}
                            disabled={isExecuting}
                            className="h-8 flex-1 min-w-0 rounded-md border border-input bg-background px-2 py-1 text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
                          >
                            <option value="auto">Auto</option>
                            {models.map((m) => (
                              <option key={m} value={m}>{m}</option>
                            ))}
                          </select>
                        </div>
                      ))}
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      The Planner numbers tasks t1…t7 in plan order, so slots it doesn’t
                      reach are ignored. Saved in this browser for future runs.
                    </p>
                  </div>
                  <div className="flex justify-end space-x-3">
                    <Button
                      variant="outline"
                      onClick={handleWebSocketSubmit}
                      disabled={!isConnected || isExecuting || !inputValue.trim()}
                      className="px-4"
                    >
                      Execute (WS)
                    </Button>
                    <Button
                      variant="default"
                      onClick={handleSubmit}
                      disabled={isExecuting || !inputValue.trim()}
                      className="px-4"
                    >
                      {isExecuting ? 'Executing...' : 'Execute (API)'}
                    </Button>
                  </div>
                </form>
              </CardContent>
            </Card>
          </div>

          {/* Final Result Panel (always present but hidden when not available) */}
          <div className="lg:col-span-3">
            <FinalResultPanel output={finalOutput} isExecuting={isExecuting} />
          </div>
        </div>

        {/* Human-in-the-loop prompts while tasks are in REQUIRES_USER_INPUT */}
        {pendingInputs.length > 0 && (
          <div className="mt-6">
            <UserInputRequestsPanel requests={pendingInputs} onSubmit={sendFeedback} />
          </div>
        )}

        {/* Plan-review gate: per-task model assignment between planning and execution */}
        {planReviewTasks && (
          <div className="mt-6">
            <PlanReviewPanel
              tasks={planReviewTasks}
              models={models}
              assignments={planAssignments}
              sent={planReviewSent}
              onAssign={handlePlanAssign}
              onStart={handleStartPlanExecution}
            />
          </div>
        )}

        {/* Execution Log Stream (remains unchanged) */}
        <div className="mt-6">
          <Card className="h-full">
            <CardHeader className="pb-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CardTitle className="text-lg font-semibold">Live Execution Stream</CardTitle>
                  {isExecuting && (
                    <Play className="h-4 w-4 animate-pulse text-primary" />
                  )}
                </div>
                <button
                  onClick={() => setExecutionLogs([])}
                  className="px-2 py-1 rounded border border-primary/20 bg-primary/10 text-primary-foreground/80 hover:bg-primary/20 text-xs"
                >
                  Clear
                </button>
              </div>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[400px] w-full bg-muted/50 rounded p-4 space-y-2">
                {executionLogs.length === 0 && !isExecuting ? (
                  <p className="text-muted-foreground text-center py-8">
                    No execution logs yet. Submit a goal to see live updates.
                  </p>
                ) : (
                  <ExecutionLogStream
                    logs={executionLogs}
                    isExecuting={isExecuting}
                  />
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        {/* History Sidebar (remains unchanged) */}
        <Sidebar className="mt-6">
          <SidebarTrigger className="w-full flex items-center justify-between px-4 py-2 bg-primary text-primary-foreground hover:bg-primary/90">
            <span className="font-medium">Execution History ({runs.length})</span>
            <ChevronRight className="transition-transform duration-200" />
          </SidebarTrigger>
          <SidebarContent className="w-64">
            <div className="space-y-2">
              {runs.map((run) => (
                <div
                  key={run.run_id}
                  className={`px-3 py-2 rounded-lg cursor-pointer hover:bg-primary/10 ${
                    selectedRunId === run.run_id ? 'bg-primary/20' : ''
                  }`}
                  onClick={() => setSelectedRunId(run.run_id)}
                >
                  <div className="flex items-start gap-2">
                    <div className="flex-shrink-0">
                      {run.status === 'completed' ? (
                        <span className="h-2.5 w-2.5 rounded-full bg-success"></span>
                      ) : run.status === 'failed' ? (
                        <span className="h-2.5 w-2.5 rounded-full bg-destructive"></span>
                      ) : (
                        <span className="h-2.5 w-2.5 rounded-full bg-warning"></span>
                      )}
                    </div>
                    <div>
                      <div className="font-medium text-sm">{run.goal}</div>
                      <div className="text-xs text-muted-foreground">
                        {run.completed_tasks}/{run.total_tasks} tasks •
                        {new Date(run.created_at).toLocaleTimeString()}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
              {runs.length === 0 && (
                <p className="text-xs text-muted-foreground text-center py-4">
                  No execution history yet
                </p>
              )}
            </div>
          </SidebarContent>
        </Sidebar>
      </div>
    </div>
  );
}