import asyncio
import os
import sys
from dotenv import load_dotenv

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.providers.base_provider import NvidiaNimProvider, ProviderConfig
from src.agents.planner_agent import PlannerAgent
from src.agents.worker_agent import WorkerAgent
from src.agents.reviewer_agent import ReviewerAgent
from src.schemas.models import Task, RunState, Goal

load_dotenv()

def _sanitize_for_print(s: str) -> str:
    """Sanitize string for printing in Windows console (cp1252 encoding)."""
    return s.encode('cp1252', errors='replace').decode('cp1252')

class AI_TEAM_ORCHESTRATOR:
    """Main orchestrator for the AI Team MVP."""

    def __init__(self) -> None:
        # Load provider configuration from environment
        model_id = os.getenv("NVIDIA_MODEL_ID", "meta/llama3-70b-instruct")
        temp = float(os.getenv("NVIDIA_TEMPERATURE", "0.7"))
        max_tok = int(os.getenv("NVIDIA_MAX_TOKENS", "1024"))
        config = ProviderConfig(
            model_id=model_id,
            temperature=temp,
            max_tokens=max_tok,
        )

        # Initialize provider
        self.provider = NvidiaNimProvider(config)

        # Initialize agents
        self.planner = PlannerAgent(self.provider)
        self.worker = WorkerAgent(self.provider)
        self.reviewer = ReviewerAgent(self.provider)

        # Initialize state
        self.state: RunState = RunState()

    async def run(self, goal: str) -> None:
        """Execute the full orchestration pipeline.

        Args:
            goal: The user's goal/request.
        """
        # Update progress
        print(f"\n[INFO] Processing goal: '{goal}'")
        self.state.current_goal = Goal(text=goal, id=self.state.run_id)
        self.state.status = "running"
        self.state.total_tasks = 0

        # Step 1: Planner decomposes the goal into tasks
        print("[INFO] Planner agent is analyzing...")
        try:
            plan_result = await self.planner.execute(goal)
        except Exception as e:
            print(f"[ERROR] Planner failed: {e}")
            return

        self.state.planner_output = plan_result
        self.state.total_tasks = len(plan_result.tasks)

        print(f"[OK] Planner generated {self.state.total_tasks} tasks:")
        for idx, task in enumerate(plan_result.tasks, 1):
            print(f"   {idx}. {_sanitize_for_print(task.description)}")

        # Update current task
        self.state.current_task = None if plan_result.tasks else None

        # Step 2: Worker executes tasks sequentially
        print("\n[INFO] Worker agent is executing tasks sequentially...")

        completed_tasks: list[Task] = []
        previous_outputs: list[str] = []

        for idx, task in enumerate(plan_result.tasks, 1):
            self.state.current_task = task

            # Inject outputs from previous tasks as context
            if previous_outputs:
                task.context = {
                    "previous_task_outputs": "\n\n".join(previous_outputs)
                }

            print(f"\n[TASK] Executing task {idx}/{self.state.total_tasks}: {_sanitize_for_print(task.description)}")

            # Worker executes task
            try:
                worker_result = await self.worker.execute(task)
            except Exception as e:
                print(f"[ERROR] Worker failed for task {task.task_id}: {e}")
                task.status = "failed"
                completed_tasks.append(task)
                continue

            task.output = worker_result.raw_output
            task.attempts = 1
            task.status = "completed"

            # Store output for future tasks
            previous_outputs.append(worker_result.raw_output)

            completed_tasks.append(task)

            print(f"[OK] Task {idx} completed")
            print(f"   Output: {_sanitize_for_print(worker_result.raw_output[:100])}...")

            # Step 3: Reviewer validates task output
            print("[INFO] Reviewer agent is validating...")

            try:
                review_result = await self.reviewer.evaluate(
                    task,
                    worker_result,
                )
            except Exception as e:
                print(f"[ERROR] Reviewer failed for task {task.task_id}: {e}")
                task.status = "failed"
                continue

            if review_result.is_valid:
                print("[OK] Task approved by reviewer")
            else:
                print(f"[FAIL] Task rejected: {_sanitize_for_print(review_result.feedback)}")
                if review_result.retry_allowed:
                    # Retry the task once
                    print("🔄 Retrying task...")

                    # Reinject context on retry
                    if previous_outputs:
                        task.context = {
                            "previous_task_outputs": "\n\n".join(previous_outputs)
                        }

                    try:
                        retry_result = await self.worker.execute(task)
                    except Exception as e:
                        print(f"[ERROR] Worker retry failed for task {task.task_id}: {e}")
                        task.status = "failed"
                        continue
                    task.output = retry_result.raw_output
                    task.attempts = 2

                    # Validate retry
                    try:
                        retry_review = await self.reviewer.evaluate(
                            task,
                            retry_result,
                        )
                    except Exception as e:
                        print(f"[ERROR] Reviewer retry failed for task {task.task_id}: {e}")
                        task.status = "failed"
                        continue

                    if retry_review.is_valid:
                        print("[OK] Task approved after retry")
                        task.status = "approved_after_retry"
                        # Replace the last task in completed_tasks with the retried one
                        completed_tasks[-1] = task
                    else:
                        print(f"[FAIL] Task still rejected after retry: {_sanitize_for_print(retry_review.feedback)}")
                        task.status = "rejected"
                else:
                    task.status = "rejected"

        # Step 4: Generate final output
        print("\n[INFO] Execution completed!")
        print("\n=== FINAL OUTPUT ===")
        print("\n[SUMMARY]")
        print(f"   Goal: {_sanitize_for_print(self.state.current_goal.text)}")
        print(f"   Total tasks: {self.state.total_tasks}")
        print(f"   Completed tasks: {len([t for t in completed_tasks if t.status in ['completed', 'approved_after_retry']])}")
        print(f"   Rejected tasks: {len([t for t in completed_tasks if t.status == 'rejected'])}")

        print("\n[TASK DETAILS]")
        for idx, task in enumerate(completed_tasks, 1):
            status_icon = "[OK]" if task.status in ["completed", "approved_after_retry"] else "[FAIL]"
            print(f"   {status_icon} {idx}. {_sanitize_for_print(task.description)}")
            print(f"      Attempts: {task.attempts}")
            if task.output:
                print(f"      Output: {_sanitize_for_print(task.output[:150])}...")

        self.state.status = "completed"

async def main():
    orchestrator = AI_TEAM_ORCHESTRATOR()
    await orchestrator.run("Design a social media database schema")

if __name__ == "__main__":
    asyncio.run(main())