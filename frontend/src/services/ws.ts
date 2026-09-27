import { useEffect, useRef, useState } from 'react';

export interface WsFrame {
  type: string;
  data?: Record<string, any>;
}

// Dev: connect to uvicorn directly. Production (Docker/nginx): same-origin
// /ws/execute, which nginx proxies (with the WS upgrade headers) to backend.
function orchestratorWsUrl(): string {
  if (import.meta.env.DEV) return 'ws://127.0.0.1:8100/ws/execute';
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws/execute`;
}

interface UseWebSocketProps {
  // Every server frame arrives here; the console reducer owns all handling.
  onEvent?: (frame: WsFrame) => void;
  onError?: (error: string) => void;
}

export function useWebSocket({ onEvent, onError }: UseWebSocketProps = {}) {
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnection] = useState<boolean>(false);
  const reconnectAttemptsRef = useRef<number>(0);
  // The server closes the socket after every run, so a close that happens
  // after run_completed is normal — not a lost connection. This flag
  // suppresses the spurious "connection error" toast in that case.
  const runCompletedRef = useRef<boolean>(false);
  const maxReconnectAttempts = 5;
  const baseDelay = 1000;

  // Latest callbacks in a ref so connect() never re-created (the mount-only
  // effect would otherwise tear down the socket on every render).
  const propsRef = useRef<UseWebSocketProps>({});
  propsRef.current = { onEvent, onError };

  useEffect(() => {
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;

      const newWs = new WebSocket(orchestratorWsUrl());

      newWs.onopen = () => {
        setIsConnection(true);
        reconnectAttemptsRef.current = 0;
        runCompletedRef.current = false;
      };

      newWs.onmessage = (event) => {
        try {
          const frame = JSON.parse(event.data) as WsFrame;
          if (frame.type === 'run_completed') runCompletedRef.current = true;
          propsRef.current.onEvent?.(frame);
          if (frame.type === 'error') {
            propsRef.current.onError?.(String(frame.data?.message ?? 'server error'));
          }
        } catch {
          propsRef.current.onError?.('Failed to parse message from server');
        }
      };

      newWs.onclose = () => {
        setIsConnection(false);
        if (reconnectAttemptsRef.current < maxReconnectAttempts) {
          const delay = baseDelay * Math.pow(2, reconnectAttemptsRef.current);
          reconnectAttemptsRef.current += 1;
          setTimeout(connect, delay);
        }
      };

      newWs.onerror = () => {
        if (!runCompletedRef.current) {
          propsRef.current.onError?.('WebSocket connection error');
        }
        setIsConnection(false);
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

  const send = (payload: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
    } else {
      onError?.('WebSocket is not connected');
    }
  };

  const sendMessage = (
    goal: string,
    apiKey?: string,
    modelSelection?: string,
    reviewPlan?: boolean,
    planModels?: Record<string, string>,
    modelKeys?: Record<string, string>,
    availableModels?: string[]
  ) => {
    const payload: Record<string, unknown> = { goal };
    // Dynamic per-run provider key; omitted => server falls back to .env.
    if (apiKey && apiKey.trim()) payload.api_key = apiKey.trim();
    // Model picker; omitted for "Auto" so the planner decides.
    if (modelSelection && modelSelection !== 'auto') payload.model_selection = modelSelection;
    // Plan-review gate: pause after planning for per-task model assignment.
    if (reviewPlan) payload.review_plan = true;
    // Pre-selected per-slot models; applied after planning without pausing.
    if (planModels && Object.keys(planModels).length > 0) payload.plan_models = planModels;
    // model -> api_key map so each task's calls use the key that served it.
    if (modelKeys && Object.keys(modelKeys).length > 0) payload.model_keys = modelKeys;
    // Fetched catalogue: whitelists the planner and arms the pre-wave
    // validation guardrail.
    if (availableModels && availableModels.length > 0) payload.available_models = availableModels;
    send(payload);
  };

  // Reply to the plan-review gate: {task_id -> model | "auto"}.
  const sendPlanAssignments = (assignments: Record<string, string>) =>
    send({ type: 'plan_model_assignment', data: { assignments } });

  // Reply to a paused task on the same open connection.
  const sendFeedback = (taskId: string, feedback: string) =>
    send({ type: 'user_feedback', data: { task_id: taskId, feedback } });

  // Abandon the live run at the next task/wave boundary.
  const sendStop = () => send({ type: 'stop_run' });

  return { sendMessage, sendFeedback, sendPlanAssignments, sendStop, isConnected };
}
