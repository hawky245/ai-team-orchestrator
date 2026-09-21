import React, { useState, useEffect, memo, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { useWebSocket } from '@/services/ws';
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
              'border-muted bg-muted/5'
            }`}>
              <div className="flex items-start gap-2">
                <div className={`flex-shrink-0 h-2.5 w-2.5 rounded-full ${
                  formatted.type === 'error' ? 'bg-destructive' :
                  formatted.type === 'success' ? 'bg-success' :
                  formatted.type === 'info' ? 'bg-primary' :
                  formatted.type === 'tool' ? 'bg-warning' :
                  formatted.type === 'retry' ? 'bg-warning' :
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

export default function App() {
  const [inputValue, setInputValue] = useState('');
  const [isExecuting, setIsExecuting] = useState(false);
  const [runs, setRuns] = useState<RunData[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [executionLogs, setExecutionLogs] = useState<ExecutionLogEntry[]>([]);
  const [finalOutput, setFinalOutput] = useState<string | null>(null);
  const { toast } = useToast();

  const { sendMessage, isConnected } = useWebSocket({
    onExecutionStart: (goal: string) => {
      setIsExecuting(true);
      setFinalOutput(null);
      setExecutionLogs([]);
      toast({
        title: "Execution Started",
        description: `Started execution for: "${goal}"`,
      });
    },
    onExecutionComplete: (summary: TaskSummary, tasks: TaskDetail[]) => {
      setIsExecuting(false);
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
    sendMessage(inputValue);
    setIsExecuting(true);
    setExecutionLogs([]);
    setInputValue('');
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