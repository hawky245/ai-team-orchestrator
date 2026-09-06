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

async def main():
    print("=== Simplified Orchestrator Test ===")

    # Load config
    model_id = os.getenv("NVIDIA_MODEL_ID", "meta/llama3-70b-instruct")
    temp = float(os.getenv("NVIDIA_TEMPERATURE", "0.7"))
    max_tok = int(os.getenv("NVIDIA_MAX_TOKENS", "1024"))
    config = ProviderConfig(model_id=model_id, temperature=temp, max_tokens=max_tok)

    # Initialize provider and agents
    provider = NvidiaNimProvider(config)
    planner = PlannerAgent(provider)
    worker = WorkerAgent(provider)
    reviewer = ReviewerAgent(provider)

    goal = "Say hello"

    # Step 1: Planner
    print("\nStep 1: Calling planner...")
    try:
        plan_result = await asyncio.wait_for(planner.execute(goal), timeout=30.0)
        print(f"[OK] Planner returned {len(plan_result.tasks)} tasks")
        for i, task in enumerate(plan_result.tasks):
            print(f"  Task {i+1}: {task.task_id} - {task.description[:50]}...")
    except Exception as e:
        print(f"[ERROR] Planner failed: {e}")
        return

    # Step 2: Execute first task only
    print("\nStep 2: Executing first task only...")
    task = plan_result.tasks[0]

    try:
        print("Calling worker...")
        worker_result = await asyncio.wait_for(worker.execute(task), timeout=30.0)
        print(f"[OK] Worker returned: {worker_result.raw_output[:100]}...")
    except Exception as e:
        print(f"[ERROR] Worker failed: {e}")
        return

    # Step 3: Review first task
    print("\nStep 3: Reviewing first task...")
    try:
        print("Calling reviewer...")
        review_result = await asyncio.wait_for(reviewer.evaluate(task, worker_result), timeout=30.0)
        print(f"[OK] Reviewer returned: approved={review_result.is_valid}, score={review_result.score}")
    except Exception as e:
        print(f"[ERROR] Reviewer failed: {e}")
        return

    print("\n=== Simplified test completed successfully ===")

if __name__ == "__main__":
    asyncio.run(main())