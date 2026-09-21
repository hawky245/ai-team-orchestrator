#!/usr/bin/env python3
# Force UTF-8 so planner/worker/reviewer outputs don't crash Windows cp1252 stdout.
import os
import sys
import io

os.environ["PYTHONIOENCODING"] = "utf-8"
# Only wrap the real process stdout (Windows cp1252 console). Under a test
# runner that has replaced sys.stdout for capture, re-wrapping its buffer
# breaks the harness ("I/O operation on closed file").
if sys.stdout is sys.__stdout__ and hasattr(sys.stdout, "buffer"):
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
Worker Agent (executes independent tasks concurrently, respecting dependencies)
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

from src.providers.base_provider import (
    NvidiaNimProvider,
    ProviderConfig,
    ProviderError,
    ModelExhaustedError,
)
from src.utils.retry import compute_delay
from src.agents.planner_agent import PlannerAgent
from src.agents.worker_agent import WorkerAgent
from src.agents.reviewer_agent import ReviewerAgent
from src.schemas.models import Task, RunState, Goal, WorkerResult
from src.database import RunModel, TaskModel, SessionLocal

load_dotenv()

app = typer.Typer(help="AI Team Orchestrator - Milestone 1 & 2")

# How long a task may sit in REQUIRES_USER_INPUT before it is failed (M9).
FEEDBACK_TIMEOUT_SECONDS = 15 * 60


def effective_dependencies(task: Task, known_ids: set) -> List[str]:
    """Task IDs that must finish before `task` may start.

    ``is_parallel`` short-circuits the list: the planner explicitly vouches
    for the task being independent, so it becomes eligible immediately.
    """
    if task.is_parallel:
        return []
    return [d for d in task.dependencies if d in known_ids and d != task.task_id]


def plan_execution_waves(
    tasks: List[Task], execution_order: str = "dag"
) -> List[List[Task]]:
    """Group tasks into dependency waves.

    Every task inside one wave is mutually independent and can be executed
    concurrently; a wave only starts after all tasks of the previous waves
    finished. Tasks referencing unknown IDs or themselves are not blocked by
    them; a dependency cycle is broken by running the leftovers together in a
    final wave instead of deadlocking.
    """
    by_id = {t.task_id: t for t in tasks}
    order = [t.task_id for t in tasks]
    known_ids = set(order)

    if execution_order == "sequential":
        return [[by_id[tid]] for tid in order]

    deps = {tid: effective_dependencies(by_id[tid], known_ids) for tid in order}
    remaining = list(order)
    done: set = set()
    waves: List[List[Task]] = []
    while remaining:
        ready = [tid for tid in remaining if all(d in done for d in deps[tid])]
        if not ready:
            ready = list(remaining)
        waves.append([by_id[tid] for tid in ready])
        done.update(ready)
        remaining = [tid for tid in remaining if tid not in done]
    return waves


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

        # Milestone 9: task_id -> {"event": asyncio.Event, "text": Optional[str]}
        # for tasks currently paused in REQUIRES_USER_INPUT.
        self._feedback_requests: Dict[str, Dict[str, Any]] = {}

    def submit_feedback(self, task_id: str, feedback: str) -> bool:
        """Deliver user feedback to a paused task.

        Called from the WebSocket input channel. Returns False when no task
        with that ID is currently waiting (stale or wrong ID).
        """
        entry = self._feedback_requests.get(task_id)
        if entry is None:
            return False
        entry["text"] = feedback
        entry["event"].set()
        return True

    async def run(self, goal: str, websocket: WebSocket = None) -> Dict[str, Any]:
        """Execute the full orchestration pipeline and return structured results."""
        # Helper to send events over websocket.
        # Milestone 8: concurrent workers emit frames from multiple coroutines;
        # Starlette WebSockets do not tolerate interleaved sends, so every
        # write is serialized through this lock.
        send_lock = asyncio.Lock()

        async def send_event(event_type: str, data: dict = None):
            if websocket is None:
                return
            event = {"type": event_type}
            if data is not None:
                event["data"] = data
            payload = json.dumps(event)
            async with send_lock:
                try:
                    await websocket.send_text(payload)
                except Exception:
                    # Client disconnected — stop the pipeline rather than keep sending
                    raise WebSocketDisconnect()

        async def pause_for_feedback(task: Task, idx: int, question: str) -> Optional[str]:
            """Park one task in REQUIRES_USER_INPUT until the user replies.

            Siblings in the same wave keep executing; the WebSocket stays
            open for both directions. Returns the feedback text, or None on
            timeout — the caller then fails just this task.
            """
            event = asyncio.Event()
            entry = {"event": event, "text": None}
            self._feedback_requests[task.task_id] = entry

            task.status = "requires_user_input"
            if run_model is not None:
                run_model.status = "requires_user_input"
                db.commit()

            await send_event("task_requires_input", {
                "task_id": task.task_id,
                "task_index": idx,
                "question": question,
            })
            print(f"[PAUSE] Task {task.task_id} awaits user feedback: {question[:80]}")

            timed_out = False
            try:
                await asyncio.wait_for(event.wait(), timeout=FEEDBACK_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                timed_out = True

            self._feedback_requests.pop(task.task_id, None)
            feedback = entry["text"]
            still_paused = bool(self._feedback_requests)

            if timed_out or feedback is None:
                await send_event("task_input_timeout", {
                    "task_id": task.task_id,
                    "task_index": idx,
                    "reason": "No user feedback received before the input window closed",
                })
                if run_model is not None:
                    run_model.status = (
                        "requires_user_input" if still_paused else "running"
                    )
                    db.commit()
                task.status = "failed"
                return None

            if run_model is not None:
                run_model.status = "requires_user_input" if still_paused else "running"
                db.commit()
            await send_event("task_resumed", {
                "task_id": task.task_id,
                "task_index": idx,
                "feedback": feedback,
            })
            print(f"[RESUME] Task {task.task_id} continuing with user feedback")
            return feedback

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

            # Step 2: Workers execute tasks concurrently wherever the
            # dependency graph allows (Milestone 8: multi-agent collaboration)
            await send_event("execution_started_tasks")

            completed_tasks: List[Task] = []
            outputs_by_id: Dict[str, str] = {}
            known_ids = {t.task_id for t in plan_result.tasks}
            task_index_by_id = {
                t.task_id: i for i, t in enumerate(plan_result.tasks, 1)
            }

            waves = plan_execution_waves(
                plan_result.tasks, plan_result.execution_order
            )
            widest = max((len(w) for w in waves), default=0)
            print(
                f"\n[INFO] Executing {self.state.total_tasks} tasks across "
                f"{len(waves)} wave(s); max concurrent workers: {widest}..."
            )

            async def run_task(task: Task, idx: int) -> None:
                """Run one task's full worker -> reviewer pipeline."""
                self.state.current_task = task

                # Inject outputs of this task's dependencies as context.
                # Independent tasks neither wait for nor see unrelated output.
                dep_notes = [
                    outputs_by_id.get(dep_id)
                    or f"[dependency {dep_id} produced no usable output]"
                    for dep_id in effective_dependencies(task, known_ids)
                ]
                if dep_notes:
                    task.context = {
                        **task.context,
                        "previous_task_outputs": "\n\n".join(dep_notes),
                    }

                async def emit_tool_execution(
                    tool_name: str, arguments: dict
                ) -> None:
                    """Broadcast this worker's tool call just before it executes."""
                    await send_event("tool_execution", {
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "task_id": task.task_id,
                    })

                await send_event("task_started", {
                    "task_index": idx,
                    "task_id": task.task_id,
                    "description": task.description
                })
                print(f"\n[TASK] Executing task {idx}/{self.state.total_tasks}: {_sanitize_for_print(task.description)}")

                # Milestone 9: planner flagged this task as ambiguous or
                # approval-gated — wait for the user before any worker call.
                if task.requires_user_input:
                    feedback = await pause_for_feedback(
                        task,
                        idx,
                        f"Task '{task.description}' needs your approval or "
                        "extra direction before the worker starts.",
                    )
                    if feedback is None:
                        completed_tasks.append(task)
                        return
                    task.context = {**task.context, "user_feedback": feedback}

                # Worker executes task with retry logic for JSON parsing errors
                worker_result = None
                last_parse_error = None

                async def emit_task_retry(attempt: int, delay: float, exc: Exception) -> None:
                    """Broadcast provider-level transient-error retries in real time."""
                    await send_event("task_retry", {
                        "task_id": task.task_id,
                        "attempt": attempt + 1,
                        "reason": f"{type(exc).__name__}: {exc} (backoff {delay:.1f}s)",
                    })

                for attempt in range(1, 4):  # up to 3 attempts with backoff
                    try:
                        # WorkerAgent.execute() already validates the JSON output
                        # via _parse_result() — no need to re-validate raw_output
                        # here (raw_output is the extracted plain text, not JSON).
                        worker_result = await self.worker.execute(
                            task,
                            on_tool_call=emit_tool_execution,
                            on_retry=emit_task_retry,
                        )
                        # Success — break out of retry loop
                        break

                    except ModelExhaustedError as e:
                        # Primary model retries (and configured fallback) spent:
                        # mark the task failed with structured state and move on.
                        print(f"[FALLBACK] Model exhausted for task {task.task_id}: {e}")
                        await send_event("task_fallback_exhausted", {
                            "task_id": task.task_id,
                            "task_index": idx,
                            "status": "failed_fallback",
                            "primary_model": e.primary_model,
                            "fallback_model": e.fallback_model,
                            "attempts": e.attempts,
                            "error": str(e.last_error),
                        })
                        task.status = "failed"
                        completed_tasks.append(task)
                        worker_result = None
                        break

                    except ProviderError as e:
                        last_parse_error = e
                        print(f"[RETRY] Worker ProviderError on attempt {attempt}/3 for task {task.task_id}: {e}")
                        if attempt < 3:
                            delay = compute_delay(attempt, base_delay=1.0)
                            await send_event("task_retry", {
                                "task_id": task.task_id,
                                "attempt": attempt + 1,
                                "reason": f"{type(e).__name__}: {e}",
                            })
                            await asyncio.sleep(delay)
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
                            delay = compute_delay(attempt, base_delay=1.0)
                            await send_event("task_retry", {
                                "task_id": task.task_id,
                                "attempt": attempt + 1,
                                "reason": f"{type(e).__name__}: {e}",
                            })
                            await asyncio.sleep(delay)
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
                    return  # task already marked failed inside the retry loop

                task.output = worker_result.raw_output
                task.attempts = attempt
                task.status = "completed"

                # Store output so dependent tasks can consume it
                outputs_by_id[task.task_id] = worker_result.raw_output
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
                    return
                except Exception as e:
                    print(f"[ERROR] Reviewer failed for task {task.task_id}: {e}")
                    await send_event("task_review_failed", {
                        "task_index": idx,
                        "task_id": task.task_id,
                        "error": str(e)
                    })
                    task.status = "failed"
                    return

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

                    # Milestone 9: reviewer declined to decide — ask the user.
                    user_feedback_given = False
                    if review_result.needs_user_input:
                        feedback = await pause_for_feedback(
                            task,
                            idx,
                            review_result.feedback
                            or f"Reviewer needs your input on '{task.description}'.",
                        )
                        if feedback is None:
                            return
                        task.context = {**task.context, "user_feedback": feedback}
                        user_feedback_given = True

                    if review_result.retry_allowed or user_feedback_given:
                        print("🔄 Retrying task...")

                        try:
                            retry_result = await self.worker.execute(
                                task,
                                on_tool_call=emit_tool_execution,
                                on_retry=emit_task_retry,
                            )
                        except ProviderError as e:
                            print(f"[ERROR] Worker retry failed (provider error) for task {task.task_id}: {e}")
                            await send_event("task_retry_worker_failed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "error": str(e)
                            })
                            task.status = "failed"
                            return
                        except Exception as e:
                            print(f"[ERROR] Worker retry failed for task {task.task_id}: {e}")
                            await send_event("task_retry_worker_failed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "error": str(e)
                            })
                            task.status = "failed"
                            return

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
                            return
                        except Exception as e:
                            print(f"[ERROR] Reviewer retry failed for task {task.task_id}: {e}")
                            await send_event("task_retry_review_failed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "error": str(e)
                            })
                            task.status = "failed"
                            return

                        if retry_review.is_valid:
                            await send_event("task_retry_review_passed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "feedback": retry_review.feedback
                            })
                            print("[OK] Task approved after retry")
                            task.status = "approved_after_retry"
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

            for wave_no, wave in enumerate(waves, 1):
                if len(wave) > 1:
                    print(
                        f"[WAVE {wave_no}] Running {len(wave)} tasks concurrently: "
                        + ", ".join(t.task_id for t in wave)
                    )
                await asyncio.gather(
                    *(run_task(t, task_index_by_id[t.task_id]) for t in wave)
                )

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

            # Emit a definitive run_completed event carrying the final polished output.
            # The end product is the *latest* completed task in plan order (with a
            # parallel engine, append order inside a wave is nondeterministic, and
            # the last task is often the review/polish task which may be rejected
            # or return an empty output). Fall back to the last planned task's
            # output if nothing completed.
            def _plan_rank(detail: Dict[str, Any]) -> int:
                return task_index_by_id.get(detail.get("task_id"), 0)

            final_output = ""
            completed = [
                t for t in task_details_list
                if t.get("status") in ("completed", "approved_after_retry")
            ]
            if completed:
                final_output = max(completed, key=_plan_rank).get("output", "") or ""
            elif task_details_list:
                final_output = max(task_details_list, key=_plan_rank).get("output", "") or ""
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