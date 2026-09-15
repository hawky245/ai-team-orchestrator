export interface RunData {
  run_id: string;
  goal: string;
  status: string;
  created_at: string;
  finished_at: string | null;
  total_tasks: number;
  completed_tasks: number;
  rejected_tasks: number;
}