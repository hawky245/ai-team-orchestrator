#!/usr/bin/env python3
# Force UTF-8 so planner/worker/reviewer outputs don't crash Windows cp1252 stdout.
import os
import sys
import io

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


"""
Main orchestrator for the AI Team MVP.

Flow:
User Goal
↓
Planner Agent
↓
Task List
↓
Worker Agent (executes tasks sequentially)
↓
Reviewer Agent (validates each task output)
↓
Final Output
"""


def _sanitize_for_print(s: str) -> str:
    """Sanitize string for printing in Windows console (cp1252 encoding)."""
    return s.encode('cp1252', errors='replace').decode('cp1252')


import asyncio
import json
import typer
from typing import List, Dict, Any, Optional
from datetime import datetime

from fastapi import WebSocket, WebSocketDisconnect
from dotenv import load_dotenv

from src.providers.base_provider import NvidiaNimProvider, ProviderConfig, ProviderError
from src.agents.planner_agent import PlannerAgent
from src.agents.worker_agent import WorkerAgent
from src.agents.reviewer_agent import ReviewerAgent
from src.schemas.models import Task, RunState, Goal, WorkerResult
from src.database import RunModel, TaskModel, SessionLocal

load_dotenv()

app = typer.Typer(help="AI Team Orchestrator - Milestone 1 & 2")


class AI_TEAM_ORCHESTRATOR:
    """Main orchestrator for the AI Team MVP."""

    def __init__(self) -> None:
        # Load provider configuration from environment
        # Generic LLM_ variables allow switching providers via .env only
        model_id = os.getenv("LLM_MODEL_ID", "openai/gpt-6-astra")
        temp = float(os.getenv("LLM_TEMPERATURE", "0.7"))
        max_tok = int(os.getenv("LLM_MAX_TOKENS", "8192"))
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

    async def run(self, goal: str, websocket: WebSocket = None) -> Dict[str, Any]:
        """Execute the full orchestration pipeline and return structured results."""
        # Helper to send events over websocket
        async def send_event(event_type: str, data: dict = None):
            if websocket is None:
                return
            event = {"type": event_type}
            if data is not None:
                event["data"] = data
            try:
                await websocket.send_text(json.dumps(event))
            except Exception:
                # Client disconnected — stop the pipeline rather than keep sending
                raise WebSocketDisconnect()

        db = None
        run_model = None
        try:
            db = SessionLocal()
            # Create and commit the initial run record
            run_model = RunModel(
                run_id=self.state.run_id,
                goal=goal,
                status="pending"
            )
            db.add(run_model)
            db.commit()

            await send_event("execution_started", {"goal": goal})
            print(f"\n[INFO] Processing goal: '{goal}'")
            self.state.current_goal = Goal(text=goal, id=self.state.run_id)
            self.state.status = "running"
            self.state.total_tasks = 0

            # Step 1: Planner decomposes the goal into tasks
            await send_event("planning_started")
            print("[INFO] Planner agent is analyzing...")
            try:
                plan_result = await self.planner.execute(goal)
            except ProviderError as e:
                print(f"[ERROR] Planner failed (provider error): {e}")
                await send_event("planning_failed", {"error": str(e)})
                if run_model is not None:
                    run_model.status = "failed"
                    run_model.finished_at = datetime.utcnow()
                    db.commit()
                return {"error": str(e), "status": "failed"}
            except Exception as e:
                print(f"[ERROR] Planner failed: {e}")
                await send_event("planning_failed", {"error": str(e)})
                if run_model is not None:
                    run_model.status = "failed"
                    run_model.finished_at = datetime.utcnow()
                    db.commit()
                return {"error": str(e), "status": "failed"}

            await send_event("planning_completed", {"task_count": len(plan_result.tasks)})
            self.state.planner_output = plan_result
            self.state.total_tasks = len(plan_result.tasks)

            # Update run with total tasks
            run_model.total_tasks = self.state.total_tasks
            db.commit()

            print(f"[OK] Planner generated {self.state.total_tasks} tasks:")
            for idx, task in enumerate(plan_result.tasks, 1):
                print(f"   {idx}. {_sanitize_for_print(task.description)}")

            self.state.current_task = None

            # Step 2: Worker executes tasks sequentially
            await send_event("execution_started_tasks")
            print("\n[INFO] Worker agent is executing tasks sequentially...")

            completed_tasks: List[Task] = []
            previous_outputs: List[str] = []

            for idx, task in enumerate(plan_result.tasks, 1):
                self.state.current_task = task

                # Inject outputs from previous tasks as context
                if previous_outputs:
                    task.context = {
                        "previous_task_outputs": "\n\n".join(previous_outputs)
                    }

                await send_event("task_started", {
                    "task_index": idx,
                    "task_id": task.task_id,
                    "description": task.description
                })
                print(f"\n[TASK] Executing task {idx}/{self.state.total_tasks}: {_sanitize_for_print(task.description)}")

                # Worker executes task with retry logic for JSON parsing errors
                worker_result = None
                last_parse_error = None

                for attempt in range(1, 4):  # up to 3 attempts with backoff
                    try:
                        # WorkerAgent.execute() already validates the JSON output
                        # via _parse_result() — no need to re-validate raw_output
                        # here (raw_output is the extracted plain text, not JSON).
                        worker_result = await self.worker.execute(task)
                        # Success — break out of retry loop
                        break

                    except ProviderError as e:
                        last_parse_error = e
                        print(f"[RETRY] Worker ProviderError on attempt {attempt}/3 for task {task.task_id}: {e}")
                        if attempt < 3:
                            await asyncio.sleep(attempt)  # backoff before retry
                            continue
                        # All attempts exhausted
                        print(f"[ERROR] Worker failed (provider error) for task {task.task_id}: {e}")
                        await send_event("task_worker_failed", {
                            "task_index": idx,
                            "task_id": task.task_id,
                            "error": str(e)
                        })
                        task.status = "failed"
                        completed_tasks.append(task)
                        # skip to next task
                        worker_result = None
                        break

                    except Exception as e:
                        last_parse_error = e
                        print(f"[RETRY] Worker unexpected error on attempt {attempt}/3 for task {task.task_id}: {type(e).__name__}: {e}")
                        if attempt < 3:
                            await asyncio.sleep(attempt)  # backoff before retry
                            continue
                        print(f"[ERROR] Worker failed for task {task.task_id}: {e}")
                        await send_event("task_worker_failed", {
                            "task_index": idx,
                            "task_id": task.task_id,
                            "error": str(e)
                        })
                        task.status = "failed"
                        completed_tasks.append(task)
                        # skip to next task
                        worker_result = None
                        break

                # Only proceed if worker_result was successfully obtained
                if worker_result is None:
                    continue  # task already marked failed inside the retry loop

                task.output = worker_result.raw_output
                task.attempts = attempt if 'attempt' in dir() else 1
                task.status = "completed"

                # Store output for future tasks
                previous_outputs.append(worker_result.raw_output)
                completed_tasks.append(task)

                await send_event("task_worker_completed", {
                    "task_index": idx,
                    "task_id": task.task_id,
                    "output": worker_result.raw_output,
                    "attempts": task.attempts
                })
                print(f"[OK] Task {idx} completed")
                print(f"   Output: {worker_result.raw_output[:100]}...")

                # Step 3: Reviewer validates task output
                await send_event("task_review_started", {
                    "task_index": idx,
                    "task_id": task.task_id
                })
                print("[INFO] Reviewer agent is validating...")

                try:
                    review_result = await self.reviewer.evaluate(
                        task,
                        worker_result,
                    )
                except ProviderError as e:
                    print(f"[ERROR] Reviewer failed (provider error) for task {task.task_id}: {e}")
                    await send_event("task_review_failed", {
                        "task_index": idx,
                        "task_id": task.task_id,
                        "error": str(e)
                    })
                    task.status = "failed"
                    continue
                except Exception as e:
                    print(f"[ERROR] Reviewer failed for task {task.task_id}: {e}")
                    await send_event("task_review_failed", {
                        "task_index": idx,
                        "task_id": task.task_id,
                        "error": str(e)
                    })
                    task.status = "failed"
                    continue

                if review_result.is_valid:
                    await send_event("task_review_passed", {
                        "task_index": idx,
                        "task_id": task.task_id,
                        "feedback": review_result.feedback
                    })
                    print("[OK] Task approved by reviewer")
                else:
                    await send_event("task_review_failed", {
                        "task_index": idx,
                        "task_id": task.task_id,
                        "feedback": review_result.feedback
                    })
                    print(f"[FAIL] Task rejected: {review_result.feedback}")
                    if review_result.retry_allowed:
                        print("🔄 Retrying task...")

                        if previous_outputs:
                            task.context = {
                                "previous_task_outputs": "\n\n".join(previous_outputs)
                            }

                        try:
                            retry_result = await self.worker.execute(task)
                        except ProviderError as e:
                            print(f"[ERROR] Worker retry failed (provider error) for task {task.task_id}: {e}")
                            await send_event("task_retry_worker_failed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "error": str(e)
                            })
                            task.status = "failed"
                            continue
                        except Exception as e:
                            print(f"[ERROR] Worker retry failed for task {task.task_id}: {e}")
                            await send_event("task_retry_worker_failed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "error": str(e)
                            })
                            task.status = "failed"
                            continue

                        task.output = retry_result.raw_output
                        task.attempts = 2

                        try:
                            retry_review = await self.reviewer.evaluate(
                                task,
                                retry_result,
                            )
                        except ProviderError as e:
                            print(f"[ERROR] Reviewer retry failed (provider error) for task {task.task_id}: {e}")
                            await send_event("task_retry_review_failed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "error": str(e)
                            })
                            task.status = "failed"
                            continue
                        except Exception as e:
                            print(f"[ERROR] Reviewer retry failed for task {task.task_id}: {e}")
                            await send_event("task_retry_review_failed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "error": str(e)
                            })
                            task.status = "failed"
                            continue

                        if retry_review.is_valid:
                            await send_event("task_retry_review_passed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "feedback": retry_review.feedback
                            })
                            print("[OK] Task approved after retry")
                            task.status = "approved_after_retry"
                            completed_tasks[-1] = task
                        else:
                            await send_event("task_retry_review_failed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "feedback": retry_review.feedback
                            })
                            print(f"[FAIL] Task still rejected after retry: {retry_review.feedback}")
                            task.status = "rejected"
                    else:
                        await send_event("task_review_failed", {
                            "task_index": idx,
                            "task_id": task.task_id,
                            "feedback": review_result.feedback
                        })
                        task.status = "rejected"

                # Save task to database
                task_model = TaskModel(
                    run_id=self.state.run_id,
                    task_id=task.task_id,
                    description=task.description,
                    status=task.status,
                    attempts=task.attempts,
                    output=task.output
                )
                db.add(task_model)

                # Update run counts
                if task.status in ['completed', 'approved_after_retry']:
                    run_model.completed_tasks += 1
                else:
                    run_model.rejected_tasks += 1
                db.commit()

            await send_event("execution_completed_tasks")
            # Step 4: Generate final output summary metrics
            print("\n[INFO] Execution completed!")
            print("\n=== FINAL OUTPUT ===")
            print("\n[SUMMARY]")
            print(f"   Goal: {_sanitize_for_print(self.state.current_goal.text)}")
            print(f"   Total tasks: {self.state.total_tasks}")
            completed_count = len([t for t in completed_tasks if t.status in ['completed', 'approved_after_retry']])
            rejected_count = len([t for t in completed_tasks if t.status == 'rejected'])
            print(f"   Completed tasks: {completed_count}")
            print(f"   Rejected tasks: {rejected_count}")

            print("\n[TASK DETAILS]")
            task_details_list = []
            for idx, task in enumerate(completed_tasks, 1):
                status_icon = "[OK]" if task.status in ["completed", "approved_after_retry"] else "[FAIL]"
                print(f"   {status_icon} {idx}. {_sanitize_for_print(task.description)}")
                print(f"      Attempts: {task.attempts}")
                if task.output:
                    print(f"      Output: {_sanitize_for_print(task.output[:150])}...")

                task_details_list.append({
                    "task_id": task.task_id,
                    "description": task.description,
                    "status": task.status,
                    "attempts": task.attempts,
                    "output": task.output
                })

            self.state.status = "completed"

            # Update run as completed
            run_model.finished_at = datetime.utcnow()
            run_model.status = "completed"
            db.commit()

            # Emit a definitive run_completed event carrying the final polished output
            # (the last completed task's output is the end product of the pipeline).
            final_output = ""
            if task_details_list:
                last_task = task_details_list[-1]
                if last_task.get("status") in ("completed", "approved_after_retry"):
                    final_output = last_task.get("output", "") or ""
            await send_event("run_completed", {
                "final_output": final_output
            })

            await send_event("execution_completed", {
                "summary": {
                    "goal": goal,
                    "total_tasks": self.state.total_tasks,
                    "completed_tasks": completed_count,
                    "rejected_tasks": rejected_count,
                    "status": "completed"
                },
                "tasks": task_details_list
            })

            return {
                "summary": {
                    "goal": goal,
                    "total_tasks": self.state.total_tasks,
                    "completed_tasks": completed_count,
                    "rejected_tasks": rejected_count,
                    "status": "completed"
                },
                "tasks": task_details_list
            }

        except Exception as e:
            # If there's an error, we try to update the run to failed and commit
            if run_model is not None:
                try:
                    run_model.status = "failed"
                    run_model.finished_at = datetime.utcnow()
                    db.commit()
                except:
                    pass
            await send_event("execution_failed", {"error": str(e)})
            raise
        finally:
            if db is not None:
                db.close()


def run_orchestrator(goal: str) -> dict:
    """Synchronous entry point used by FastAPI backend."""
    orchestrator = AI_TEAM_ORCHESTRATOR()
    return asyncio.run(orchestrator.run(goal))


@app.command()
def execute(goal: str = typer.Argument(..., help="The user's goal or request")):
    """Execute the AI team orchestration pipeline via CLI."""
    run_orchestrator(goal)


if __name__ == "__main__":
    app()