// TypeScript interfaces/types for the incoming orchestrator JSON events (planner, worker, reviewer)

export interface OrchestratorEvent {
  type: string;
  data?: Record<string, any>;
}

export interface ExecutionStartedEvent extends OrchestratorEvent {
  type: 'execution_started';
  data: {
    goal: string;
  };
}

export interface ExecutionFailedEvent extends OrchestratorEvent {
  type: 'execution_failed';
  data: {
    error: string;
  };
}

export interface ExecutionCompletedEvent extends OrchestratorEvent {
  type: 'execution_completed';
  data: {
    summary: {
      goal: string;
      total_tasks: number;
      completed_tasks: number;
      rejected_tasks: number;
      status: string;
    };
    tasks: TaskDetail[];
  };
}

export interface PlanningStartedEvent extends OrchestratorEvent {
  type: 'planning_started';
}

export interface PlanningCompletedEvent extends OrchestratorEvent {
  type: 'planning_completed';
  data: {
    task_count: number;
  };
}

export interface TaskStartedEvent extends OrchestratorEvent {
  type: 'task_started';
  data: {
    task_index: number;
    task_id: string;
    description: string;
  };
}

export interface TaskWorkerCompletedEvent extends OrchestratorEvent {
  type: 'task_worker_completed';
  data: {
    task_index: number;
    task_id: string;
    output: string;
    attempts: number;
  };
}

export interface TaskReviewPassedEvent extends OrchestratorEvent {
  type: 'task_review_passed';
  data: {
    task_index: number;
    task_id: string;
    feedback: string;
  };
}

export interface TaskReviewFailedEvent extends OrchestratorEvent {
  type: 'task_review_failed';
  data: {
    task_index: number;
    task_id: string;
    feedback: string;
  };
}

export interface TaskDetail {
  task_id: string;
  description: string;
  status: string;
  attempts: number;
  output: string;
}

export interface RunCompletedEvent extends OrchestratorEvent {
  type: 'run_completed';
  data: {
    final_output: string;
  };
}

export interface ToolExecutionEvent extends OrchestratorEvent {
  type: 'tool_execution';
  data: {
    tool_name: string;
    arguments: Record<string, unknown>;
    task_id?: string;
  };
}

export interface TaskRetryEvent extends OrchestratorEvent {
  type: 'task_retry';
  data: {
    task_id: string;
    attempt: number;
    reason: string;
  };
}