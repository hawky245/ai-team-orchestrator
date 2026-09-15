import { useEffect, useRef, useState } from 'react';
import { OrchestratorEvent, ExecutionStartedEvent, ExecutionCompletedEvent,
         PlanningStartedEvent, PlanningCompletedEvent, TaskStartedEvent,
         TaskWorkerCompletedEvent, TaskReviewPassedEvent, TaskReviewFailedEvent }
  from '../types/events';

interface UseWebSocketProps {
  onEvent?: (event: OrchestratorEvent) => void;
  onExecutionStart?: (goal: string) => void;
  onExecutionComplete?: (summary: any, tasks: any[]) => void;
  onRunComplete?: (finalOutput: string) => void;
  onPlanningStart?: () => void;
  onPlanningComplete?: (taskCount: number) => void;
  onTaskStart?: (taskIndex: number, taskId: string, description: string) => void;
  onTaskWorkerComplete?: (taskIndex: number, taskId: string, output: string, attempts: number) => void;
  onTaskReviewPassed?: (taskIndex: number, taskId: string, feedback: string) => void;
  onTaskReviewFailed?: (taskIndex: number, taskId: string, feedback: string) => void;
  onError?: (error: string) => void;
  onConnectionChange?: (isConnected: boolean) => void;
}

export function useWebSocket({
  onEvent,
  onExecutionStart,
  onExecutionComplete,
  onPlanningStart,
  onPlanningComplete,
  onTaskStart,
  onTaskWorkerComplete,
  onTaskReviewPassed,
  onTaskReviewFailed,
  onError,
  onConnectionChange
}: UseWebSocketProps = {}) {
  // Keep the WebSocket in a ref so it survives renders and doesn't trigger
  // the effect below to re-run on every render.
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnection] = useState<boolean>(false);
  const reconnectAttemptsRef = useRef<number>(0);
  const maxReconnectAttempts = 5;
  const baseDelay = 1000;

  // Stash the latest callbacks in refs so the connect function can always call
  // the most recent handlers without depending on them (which would otherwise
  // recreate the connect function and re-run the effect on every render).
  const propsRef = useRef<UseWebSocketProps>({} as UseWebSocketProps);
  propsRef.current = {
    onEvent, onExecutionStart, onExecutionComplete, onPlanningStart,
    onPlanningComplete, onTaskStart, onTaskWorkerComplete, onTaskReviewPassed,
    onTaskReviewFailed, onError, onConnectionChange
  };

  useEffect(() => {
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;

      const newWs = new WebSocket('ws://127.0.0.1:8000/ws/execute');

      newWs.onopen = () => {
        console.log('WebSocket connected');
        setIsConnection(true);
        reconnectAttemptsRef.current = 0;
        propsRef.current.onConnectionChange?.(true);
      };

      newWs.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          propsRef.current.onEvent?.(data);

          switch (data.type) {
            case 'execution_started':
              propsRef.current.onExecutionStart?.(data.data.goal);
              break;
            case 'execution_completed':
              propsRef.current.onExecutionComplete?.(data.data.summary, data.data.tasks);
              break;
            case 'run_completed':
              propsRef.current.onRunComplete?.(data.data.final_output);
              break;
            case 'planning_started':
              propsRef.current.onPlanningStart?.();
              break;
            case 'planning_completed':
              propsRef.current.onPlanningComplete?.(data.data.task_count);
              break;
            case 'task_started':
              propsRef.current.onTaskStart?.(data.data.task_index, data.data.task_id, data.data.description);
              break;
            case 'task_worker_completed':
              propsRef.current.onTaskWorkerComplete?.(
                data.data.task_index,
                data.data.task_id,
                data.data.output,
                data.data.attempts
              );
              break;
            case 'task_review_passed':
              propsRef.current.onTaskReviewPassed?.(
                data.data.task_index,
                data.data.task_id,
                data.data.feedback
              );
              break;
            case 'task_review_failed':
              propsRef.current.onTaskReviewFailed?.(
                data.data.task_index,
                data.data.task_id,
                data.data.feedback
              );
              break;
            case 'error':
              propsRef.current.onError?.(data.data.message);
              break;
          }
        } catch (error) {
          console.error('Error parsing WebSocket message:', error);
          propsRef.current.onError?.('Failed to parse message from server');
        }
      };

      newWs.onclose = () => {
        console.log('WebSocket disconnected');
        setIsConnection(false);
        propsRef.current.onConnectionChange?.(false);

        // Only reconnect if we haven't hit the cap and the component is still mounted
        if (reconnectAttemptsRef.current < maxReconnectAttempts) {
          const delay = baseDelay * Math.pow(2, reconnectAttemptsRef.current);
          reconnectAttemptsRef.current += 1;
          setTimeout(connect, delay);
        }
      };

      newWs.onerror = (error) => {
        console.error('WebSocket error:', error);
        propsRef.current.onError?.('WebSocket connection error');
        setIsConnection(false);
        propsRef.current.onConnectionChange?.(false);
      };

      wsRef.current = newWs;
    };

    connect();

    return () => {
      cancelled = true;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sendMessage = (goal: string) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ goal }));
    } else {
      onError?.('WebSocket is not connected');
    }
  };

  return {
    sendMessage,
    isConnected,
    reconnectAttempts: reconnectAttemptsRef.current
  };
}