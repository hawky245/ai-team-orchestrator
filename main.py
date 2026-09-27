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
from src.schemas.models import Task, RunState, WorkerResult
from src.database import RunModel, TaskModel, SessionLocal

load_dotenv()


# How long a task may sit in REQUIRES_USER_INPUT before it is failed (M9).
FEEDBACK_TIMEOUT_SECONDS = 15 * 60
# Step-level model rotation: max distinct models tried for one task.
MAX_STEP_MODELS = 3

# Model-id heuristics for "can this thing hold a text conversation". Catalogues
# are alphabetical, and the alphabet starts with whisper/orpheus/fuyu — pinning
# a worker to the first entry guarantees failure. Non-chat hints win on
# conflict (e.g. "llama-guard" contains "llama" but is a classifier).
_NON_CHAT_HINTS = (
    "whisper", "orpheus", "tts", "audio", "speech", "asr", "stt", "embed",
    "clip", "fuyu", "llava", "nvl-", "rerank", "guard", "classifier",
    "image", "diffusion", "flux", "sd-", "sdxl", "dall", "transcribe",
    "segment", "detect", "yolo", "bge", "e5", "nv-embed", "prometheus",
)
_CHAT_HINTS = (
    "llama", "mistral", "mixtral", "qwen", "gemma", "deepseek", "nemotron",
    "nova", "phi", "gpt", "claude", "gemini", "command", "yi-", "zephyr",
    "openhermes", "internlm", "glm", "kimi", "grok", "polar", "eagle",
    "magistral", "devstral", "trinity", "tulu", "wizard", "nous",
)


def _chat_capable(model_id: str) -> bool:
    mid = (model_id or "").lower()
    if any(h in mid for h in _NON_CHAT_HINTS):
        return False
    return any(h in mid for h in _CHAT_HINTS)


def _rank_models(models: List[str]) -> List[str]:
    """Stable sort: known chat families first, unknowns next, obvious
    non-text models last."""
    known = [m for m in models if _chat_capable(m)]
    unknown = [
        m for m in models
        if m not in known and not any(h in m.lower() for h in _NON_CHAT_HINTS)
    ]
    rest = [m for m in models if m not in known and m not in unknown]
    return known + unknown + rest


def effective_dependencies(task: Task, known_ids: set) -> List[str]:
    """Task IDs that must finish before `task` may start."""
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

    def __init__(self, api_key: Optional[str] = None) -> None:
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

        # Initialize provider. A user-supplied key (dashboard/API) overrides
        # the .env keys for every agent, which all share this provider.
        # NOTE: never log api_key.
        self.provider = NvidiaNimProvider(config, api_key=api_key)

        # Initialize agents
        self.planner = PlannerAgent(self.provider)
        self.worker = WorkerAgent(self.provider)
        self.reviewer = ReviewerAgent(self.provider)

        # Initialize state
        self.state: RunState = RunState()

        # Milestone 9: task_id -> {"event": asyncio.Event, "text": Optional[str]}
        # for tasks currently paused in REQUIRES_USER_INPUT.
        self._feedback_requests: Dict[str, Dict[str, Any]] = {}

        # Milestone 21: dashboard STOP button. Set to abandon the run between
        # tasks/waves; in-flight LLM calls finish, nothing after them starts.
        self._stop_event: asyncio.Event = asyncio.Event()

        # Milestone 13: pending plan-review gate. {"event", "assignments"} where
        # assignments maps task_id -> chosen model string ("auto"/absent keeps
        # the planner's routing). Set while run() waits after planning.
        self._plan_review: Optional[Dict[str, Any]] = None

    def request_stop(self) -> None:
        """Ask the live run to stop at the next task/wave boundary."""
        self._stop_event.set()

    def submit_plan_assignments(self, assignments: Dict[str, Any]) -> bool:
        """Deliver user-chosen per-task models to the paused plan-review gate.

        Called from the WebSocket input channel when run() is waiting after
        planning. Returns False if no plan review is currently pending.
        """
        entry = self._plan_review
        if entry is None:
            return False
        entry["assignments"] = assignments or {}
        entry["event"].set()
        return True

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

    async def run(
        self,
        goal: str,
        websocket: WebSocket = None,
        model_selection: Optional[str] = None,
        review_plan: bool = False,
        plan_models: Optional[Dict[str, str]] = None,
        model_keys: Optional[Dict[str, str]] = None,
        available_models: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute the full orchestration pipeline and return structured results."""
        # Multi-key runs: route each model's calls to the API key that served
        # it from the dashboard's Model Bay.
        self.provider.model_keys = dict(model_keys or {})
        self._stop_event.clear()

        # Model availability whitelist (the dashboard's fetched catalogue):
        # the planner may only reference these, and every assignment is
        # validated against it before any wave runs. Empty/None disables it.
        avail: List[str] = []
        for m in available_models or []:
            s = str(m).strip()
            if s and s not in avail:
                avail.append(s)
        # getattr: offline test fakes may not carry a ProviderConfig.
        env_model = getattr(
            getattr(self.provider, "config", None), "model_id", ""
        )

        def _availability_default() -> str:
            """Primary default the guardrail falls back to: the env model
            when it is servable, then the configured fallback, then the
            first CHAT-CAPABLE model of the fetched list (catalogues are
            alphabetical — first entry is often a transcription model),
            then the raw first entry."""
            if not avail:
                return env_model
            if env_model in avail:
                return env_model
            fb = os.getenv("LLM_FALLBACK_MODEL_ID", "").strip()
            if fb and fb in avail:
                return fb
            for m in _rank_models(avail):
                if _chat_capable(m):
                    return m
            return avail[0]

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

        async def pause_for_feedback(task: Task, idx: int, question: str,
                                     context: Optional[Dict[str, str]] = None) -> Optional[str]:
            """Park one task in REQUIRES_USER_INPUT until the user replies.

            Siblings in the same wave keep executing; the WebSocket stays
            open for both directions. Returns the feedback text, or None on
            timeout — the caller then fails just this task. `context` carries
            the dependency outputs the decision depends on, so the dashboard
            can show the real options instead of a bare approval prompt.
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
                "context": context or {},
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
            self.state.current_goal = goal
            self.state.status = "running"
            self.state.total_tasks = 0

            # Step 1: Planner decomposes the goal into tasks.
            # A run-level model choice ALSO routes the planner call: a user
            # key without access to the server's env model would otherwise
            # die here, before any task-level routing could help. When an
            # availability whitelist is present the planner call must land
            # on a servable model too — a guardrail applied after planning
            # would arrive too late for this very call.
            forced_model = (model_selection or "").strip()
            if forced_model.lower() == "auto":
                forced_model = ""
            planner_model = None
            if avail:
                if forced_model and forced_model in avail:
                    planner_model = forced_model
                elif forced_model:
                    print(
                        f"[WARN] Forced model '{forced_model}' is not in the "
                        f"available list; planner routed to "
                        f"'{_availability_default()}'"
                    )
                    planner_model = _availability_default()
                elif env_model not in avail:
                    planner_model = _availability_default()
                else:
                    planner_model = env_model
            elif forced_model:
                planner_model = forced_model
            await send_event("planning_started")
            print(
                "[INFO] Planner agent is analyzing"
                + (f" via '{planner_model}'" if planner_model else "")
                + "..."
            )
            try:
                plan_result = await self.planner.execute(
                    goal,
                    model=planner_model,
                    available_models=avail or None,
                )
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

            await send_event("planning_completed", {
                "task_count": len(plan_result.tasks),
                # Graph shape for the dashboard canvas: the DAG exists even when
                # the plan-review gate is off, so ship the same task summaries.
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "task_index": i,
                        "description": t.description,
                        "depends_on": list(t.dependencies),
                        "planner_model": t.model_override or "auto",
                        "requires_user_input": t.requires_user_input,
                    }
                    for i, t in enumerate(plan_result.tasks, 1)
                ],
            })
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

            completed_tasks: List[Task] = []
            outputs_by_id: Dict[str, str] = {}
            known_ids = {t.task_id for t in plan_result.tasks}
            task_index_by_id = {
                t.task_id: i for i, t in enumerate(plan_result.tasks, 1)
            }

            # Dynamic model selection: when the dashboard forces a specific
            # model, override every task's planner-chosen model_override before
            # the wave scheduler runs. "Auto"/empty leaves planner routing intact.
            # (forced_model was already parsed above and used for the planner.)
            if forced_model:
                for _t in plan_result.tasks:
                    _t.model_override = forced_model
                await send_event("model_selection", {
                    "model": forced_model,
                    "task_count": len(plan_result.tasks),
                })
                print(
                    f"[INFO] Model selection: forced all "
                    f"{len(plan_result.tasks)} tasks onto '{forced_model}'"
                )

            # Pre-selected per-task models (chosen in the dashboard BEFORE the
            # run, keyed by task id t1..t7): applied right after planning, so
            # nothing has to pause mid-run. Beats the run-level forced choice
            # for the tasks it names; unknown ids are simply ignored.
            preselected = 0
            if plan_models:
                for _t in plan_result.tasks:
                    chosen = str(plan_models.get(_t.task_id, "")).strip()
                    if chosen and chosen.lower() != "auto":
                        _t.model_override = chosen
                        preselected += 1
                if preselected:
                    await send_event("plan_models_applied", {
                        "assigned": preselected,
                        "assignments": {
                            _t.task_id: _t.model_override or "auto"
                            for _t in plan_result.tasks
                        },
                    })
                    print(
                        f"[INFO] Pre-selected models applied to {preselected} "
                        "task slot(s)"
                    )

            # Plan-review gate (dashboard "assign a model per task"): hold the
            # (still open) WebSocket after planning so the user can attach a
            # model to each task before any worker starts. A timeout degrades
            # to the planner's own routing rather than failing the run.
            if review_plan:
                review_event = asyncio.Event()
                self._plan_review = {"event": review_event, "assignments": None}
                if run_model is not None:
                    run_model.status = "requires_user_input"
                    db.commit()
                await send_event("plan_review_requested", {
                    "task_count": len(plan_result.tasks),
                    "tasks": [
                        {
                            "task_id": t.task_id,
                            "task_index": task_index_by_id[t.task_id],
                            "description": t.description,
                            "depends_on": effective_dependencies(t, known_ids),
                            "planner_model": t.model_override or "auto",
                            "requires_user_input": t.requires_user_input,
                        }
                        for t in plan_result.tasks
                    ],
                })
                print("[PAUSE] Plan review requested; awaiting per-task model assignments")

                review_timed_out = False
                try:
                    await asyncio.wait_for(
                        review_event.wait(), timeout=FEEDBACK_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    review_timed_out = True

                assignments = (self._plan_review or {}).get("assignments") or {}
                self._plan_review = None
                if run_model is not None:
                    run_model.status = "running"
                    db.commit()

                if review_timed_out:
                    await send_event("plan_review_timeout", {
                        "reason": "No assignments received; proceeding with planner routing",
                    })
                    print("[TIMEOUT] Plan review window closed; using planner models")
                else:
                    assigned = 0
                    for t in plan_result.tasks:
                        chosen = str(assignments.get(t.task_id, "")).strip()
                        if chosen and chosen.lower() != "auto":
                            t.model_override = chosen
                            assigned += 1
                    await send_event("plan_review_completed", {
                        "assigned": assigned,
                        "assignments": {
                            t.task_id: t.model_override or "auto"
                            for t in plan_result.tasks
                        },
                    })
                    print(
                        f"[OK] Plan review done: {assigned} task(s) given "
                        "user-chosen models"
                    )

            # Availability guardrail: the LAST gate before any wave runs.
            # Every task model — planner-picked, run-level forced, pre-
            # selected slot, or review-assigned — must be in the fetched
            # catalogue or it is corrected to the primary default. Tasks
            # left on "auto" (None) also get pinned when the env default
            # is not servable, since they would 404 on that model anyway.
            if avail:
                fallback_model = _availability_default()
                corrected = []
                for t in plan_result.tasks:
                    m = t.model_override
                    if m is None:
                        if env_model not in avail:
                            t.model_override = fallback_model
                            corrected.append(
                                {"task_id": t.task_id, "from": "auto",
                                 "to": fallback_model}
                            )
                    elif m not in avail:
                        t.model_override = fallback_model
                        corrected.append(
                            {"task_id": t.task_id, "from": m,
                             "to": fallback_model}
                        )
                if corrected:
                    await send_event("model_guardrail", {
                        "fallback_model": fallback_model,
                        "available_count": len(avail),
                        "corrected": corrected,
                    })
                    print(
                        f"[WARN] Model guardrail corrected {len(corrected)} "
                        f"task(s) outside the available list -> "
                        f"'{fallback_model}'"
                    )

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
                if self._stop_event.is_set():
                    task.status = "failed"
                    completed_tasks.append(task)
                    await send_event("task_worker_failed", {
                        "task_index": idx, "task_id": task.task_id,
                        "error": "stopped by user",
                    })
                    return
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
                    dep_context = {
                        dep_id: (outputs_by_id.get(dep_id) or "")[:1200]
                        for dep_id in effective_dependencies(task, known_ids)
                        if outputs_by_id.get(dep_id)
                    }
                    feedback = await pause_for_feedback(
                        task,
                        idx,
                        f"Task '{task.description}' needs your approval or "
                        "extra direction before the worker starts.",
                        context=dep_context,
                    )
                    if feedback is None:
                        completed_tasks.append(task)
                        return
                    task.context = {**task.context, "user_feedback": feedback}

                # Worker executes task with retry logic for JSON parsing errors

                async def emit_task_retry(attempt: int, delay: float, exc: Exception) -> None:
                    """Broadcast provider-level transient-error retries in real time."""
                    await send_event("task_retry", {
                        "task_id": task.task_id,
                        "attempt": attempt + 1,
                        "reason": f"{type(exc).__name__}: {exc} (backoff {delay:.1f}s)",
                    })

                # Step-level model rotation: when this step permanently fails
                # on its assigned model — worker errors out OR the reviewer
                # rejects the output — re-run the SAME step on the next model
                # from the fetched availability list before giving up. The
                # first candidate stays the task's own pick (None = provider
                # default), so untouched routing is not rewritten.
                step_models = [task.model_override]
                for _m in _rank_models(avail):
                    if _m in step_models:
                        continue
                    if _m == env_model and step_models[0] is None:
                        continue  # same model the None default resolves to
                    step_models.append(_m)
                step_models = step_models[:MAX_STEP_MODELS]

                appended = False
                for sm_idx, step_model in enumerate(step_models):
                    can_rotate = sm_idx < len(step_models) - 1
                    task.model_override = step_model
                    rotate_reason = "worker failed"
                    worker_result = None

                    for attempt in range(1, 4):  # up to 3 attempts per model
                        try:
                            # WorkerAgent.execute() already validates the JSON
                            # output via _parse_result() — raw_output is the
                            # extracted plain text, not JSON.
                            worker_result = await self.worker.execute(
                                task,
                                on_tool_call=emit_tool_execution,
                                on_retry=emit_task_retry,
                            )
                            break

                        except ModelExhaustedError as e:
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
                            rotate_reason = f"'{e.primary_model}' exhausted: {e.last_error}"
                            worker_result = None
                            break

                        except ProviderError as e:
                            print(f"[RETRY] Worker ProviderError on attempt {attempt}/3 for task {task.task_id}: {e}")
                            rotate_reason = f"worker error: {e}"
                            if attempt < 3:
                                delay = compute_delay(attempt, base_delay=1.0)
                                await send_event("task_retry", {
                                    "task_id": task.task_id,
                                    "attempt": attempt + 1,
                                    "reason": f"{type(e).__name__}: {e}",
                                })
                                await asyncio.sleep(delay)
                                continue
                            worker_result = None
                            break

                        except Exception as e:
                            print(f"[RETRY] Worker unexpected error on attempt {attempt}/3 for task {task.task_id}: {type(e).__name__}: {e}")
                            rotate_reason = f"worker error: {type(e).__name__}: {e}"
                            if attempt < 3:
                                delay = compute_delay(attempt, base_delay=1.0)
                                await send_event("task_retry", {
                                    "task_id": task.task_id,
                                    "attempt": attempt + 1,
                                    "reason": f"{e}",
                                })
                                await asyncio.sleep(delay)
                                continue
                            worker_result = None
                            break

                    if worker_result is None:
                        if can_rotate:
                            nxt = step_models[sm_idx + 1]
                            print(f"[ROTATE] {task.task_id}: '{step_model}' failed ({rotate_reason}); repeating step on '{nxt}'")
                            await send_event("task_model_retry", {
                                "task_id": task.task_id,
                                "task_index": idx,
                                "from_model": step_model,
                                "to_model": nxt,
                                "reason": rotate_reason,
                            })
                            continue
                        print(f"[ERROR] Worker failed for task {task.task_id}: {rotate_reason}")
                        await send_event("task_worker_failed", {
                            "task_index": idx,
                            "task_id": task.task_id,
                            "error": rotate_reason,
                        })
                        task.status = "failed"
                        completed_tasks.append(task)
                        return

                    task.output = worker_result.raw_output
                    task.attempts = attempt
                    task.status = "completed"

                    # Store output so dependent tasks can consume it
                    outputs_by_id[task.task_id] = worker_result.raw_output
                    if not appended:
                        completed_tasks.append(task)
                        appended = True

                    await send_event("task_worker_completed", {
                        "task_index": idx,
                        "task_id": task.task_id,
                        "output": worker_result.raw_output,
                        "attempts": task.attempts,
                        "model": step_model,
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
                        break  # step passed on this model

                    await send_event("task_review_failed", {
                        "task_index": idx,
                        "task_id": task.task_id,
                        "feedback": review_result.feedback
                    })
                    print(f"[FAIL] Task rejected: {review_result.feedback}")

                    # Milestone 9: reviewer declined to decide — ask the user.
                    retry_reason = None
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
                        retry_reason = "user feedback"
                    elif review_result.retry_allowed:
                        retry_reason = "retry allowed"

                    if retry_reason:
                        print(f"🔄 Retrying task on '{step_model}' ({retry_reason})...")
                        try:
                            retry_result = await self.worker.execute(
                                task,
                                on_tool_call=emit_tool_execution,
                                on_retry=emit_task_retry,
                            )
                            retry_review = await self.reviewer.evaluate(
                                task,
                                retry_result,
                            )
                            task.output = retry_result.raw_output
                            task.attempts = 2
                            outputs_by_id[task.task_id] = retry_result.raw_output
                            if retry_review.is_valid:
                                await send_event("task_retry_review_passed", {
                                    "task_index": idx,
                                    "task_id": task.task_id,
                                    "feedback": retry_review.feedback
                                })
                                print("[OK] Task approved after retry")
                                task.status = "approved_after_retry"
                                break  # passed; leave the rotation loop
                            await send_event("task_retry_review_failed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "feedback": retry_review.feedback
                            })
                            rotate_reason = f"reviewer rejected: {retry_review.feedback}"
                        except (ProviderError, Exception) as e:
                            print(f"[ERROR] Worker/reviewer retry failed for task {task.task_id}: {e}")
                            await send_event("task_retry_worker_failed", {
                                "task_index": idx,
                                "task_id": task.task_id,
                                "error": str(e)
                            })
                            rotate_reason = f"same-model retry failed: {e}"
                    else:
                        rotate_reason = f"reviewer rejected: {review_result.feedback}"

                    # Rejected on this model: rotate to the next one or give up.
                    if can_rotate:
                        nxt = step_models[sm_idx + 1]
                        print(f"[ROTATE] {task.task_id}: rejected on '{step_model}'; repeating step on '{nxt}'")
                        await send_event("task_model_retry", {
                            "task_id": task.task_id,
                            "task_index": idx,
                            "from_model": step_model,
                            "to_model": nxt,
                            "reason": rotate_reason,
                        })
                        continue

                    print(f"[FAIL] Task still rejected after all models: {rotate_reason}")
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
                if self._stop_event.is_set():
                    await send_event("execution_failed", {
                        "error": "stopped by user before wave "
                                 f"{wave_no} of {len(waves)}"
                    })
                    if run_model is not None:
                        run_model.status = "failed"
                        run_model.finished_at = datetime.utcnow()
                        db.commit()
                    self.state.status = "failed"
                    return {"error": "stopped by user", "status": "stopped"}
                if len(wave) > 1:
                    print(
                        f"[WAVE {wave_no}] Running {len(wave)} tasks concurrently: "
                        + ", ".join(t.task_id for t in wave)
                    )
                await asyncio.gather(
                    *(run_task(t, task_index_by_id[t.task_id]) for t in wave)
                )

            # Step 4: Generate final output summary metrics
            print("\n[INFO] Execution completed!")
            print("\n=== FINAL OUTPUT ===")
            print("\n[SUMMARY]")
            print(f"   Goal: {_sanitize_for_print(self.state.current_goal)}")
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

            # One completion frame: the final polished output plus the
            # summary. The end product is the *latest* completed task in plan
            # order (with a parallel engine, append order inside a wave is
            # nondeterministic, and the last task is often the review/polish
            # task which may be rejected or return an empty output). Fall
            # back to the last planned task's output if nothing completed.
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

            summary = {
                "goal": goal,
                "total_tasks": self.state.total_tasks,
                "completed_tasks": completed_count,
                "rejected_tasks": rejected_count,
                "status": "completed",
            }
            await send_event("run_completed", {
                "final_output": final_output,
                "summary": summary,
                "tasks": task_details_list,
            })

            return {"summary": summary, "tasks": task_details_list}

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
