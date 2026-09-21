"""Milestone 9 tests: human-in-the-loop pause / resume.

Covers flag parsing (planner + reviewer), the offline end-to-end pause
flows (planner-flagged pre-execution pause, reviewer-flagged post-review
pause, feedback injection into agent context, dependent-task blocking,
sibling tasks continuing while one is parked), the timeout path, and
feedback routed to unknown task IDs.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_feedback.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

import main as orch_main
from src.agents.planner_agent import PlannerAgent
from src.agents.reviewer_agent import ReviewerAgent
from src.providers.base_provider import ProviderResponse


# ---------------------------------------------------------------------------
# 1. Flag parsing
# ---------------------------------------------------------------------------

def test_flag_parsing():
    planner = PlannerAgent.__new__(PlannerAgent)
    plan = planner._parse_plan(json.dumps({
        "summary": "s", "execution_order": "dag",
        "tasks": [
            {"task_id": "t1", "description": "a", "requires_user_input": True},
            {"task_id": "t2", "description": "b"},
            {"task_id": "t3", "description": "c", "requires_user_input": False},
        ],
    }))
    assert plan.tasks[0].requires_user_input is True
    assert plan.tasks[1].requires_user_input is False  # optional, defaults off
    assert plan.tasks[2].requires_user_input is False

    reviewer = ReviewerAgent.__new__(ReviewerAgent)
    r = reviewer._parse_result(json.dumps({
        "approved": False, "feedback": "which tone?", "requires_user_input": True
    }))
    assert r.needs_user_input is True and r.is_valid is False
    r = reviewer._parse_result(json.dumps({"approved": True, "feedback": "ok"}))
    assert r.needs_user_input is False
    print("PASS  planner/reviewer parse requires_user_input flags")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

WORKER_DELAY = 0.05


class FakeProvider:
    """Scripted provider: per-description worker behaviour + reviewer verdicts."""

    def __init__(self, plan: dict, reviewer_script: dict):
        self.plan = plan
        self.reviewer_script = reviewer_script  # desc -> list of verdict dicts
        self.worker_prompts: dict[str, list[str]] = {}
        self.review_counts: dict[str, int] = {}

    @staticmethod
    def _resp(content: str) -> ProviderResponse:
        return ProviderResponse(content=content, finish_reason="stop")

    async def generate(self, system_prompt, user_prompt, schema=None, **kwargs):
        if "Planner Agent" in system_prompt:
            return self._resp(json.dumps(self.plan))
        if "Reviewer Agent" in system_prompt:
            desc = next(
                d for d in self.reviewer_script
                if d in user_prompt.split("Worker Output")[0]
            )
            verdicts = self.reviewer_script[desc]
            idx = self.review_counts.get(desc, 0)
            self.review_counts[desc] = idx + 1
            return self._resp(json.dumps(verdicts[min(idx, len(verdicts) - 1)]))
        desc = user_prompt.split("\n")[1].replace("Task Description:", "").strip()
        self.worker_prompts.setdefault(desc, []).append(user_prompt)
        await asyncio.sleep(WORKER_DELAY)
        return self._resp(json.dumps({"raw_output": f"OUT[{desc}]", "artifacts": []}))


class FakeWebSocket:
    """Lock-violation-aware frame recorder with a predicate waiter."""

    def __init__(self):
        self.frames: list[dict] = []
        self.inflight = 0

    async def send_text(self, payload):
        self.inflight += 1
        if self.inflight > 1:
            raise AssertionError("CONCURRENT WEBSOCKET SEND DETECTED (lock missing)")
        try:
            await asyncio.sleep(0.001)
            frame = json.loads(payload)
            assert "type" in frame, frame
            self.frames.append(frame)
        finally:
            self.inflight -= 1

    def has(self, pred):
        return next((f for f in self.frames if pred(f)), None)

    async def wait_for(self, pred, timeout=5.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            frame = self.has(pred)
            if frame:
                return frame
            await asyncio.sleep(0.01)
        raise AssertionError(
            f"no frame matched within {timeout}s; saw: "
            + ", ".join(f["type"] + ":" + str(f.get("data", {}).get("task_id", ""))
                        for f in self.frames)
        )


class FakeSession:
    def add(self, obj):
        for attr in ("total_tasks", "completed_tasks", "rejected_tasks"):
            if hasattr(obj, attr) and getattr(obj, attr) is None:
                setattr(obj, attr, 0)

    def commit(self):
        pass

    def close(self):
        pass


def build_orch(plan, reviewer_script):
    orch_main.SessionLocal = FakeSession
    provider = FakeProvider(plan, reviewer_script)
    ws = FakeWebSocket()
    orchestrator = orch_main.AI_TEAM_ORCHESTRATOR()
    orchestrator.provider = provider
    orchestrator.planner.provider = provider
    orchestrator.worker.provider = provider
    orchestrator.reviewer.provider = provider
    return orchestrator, provider, ws


PAUSE_PLAN = {
    "summary": "pause demo",
    "execution_order": "dag",
    "tasks": [
        {"task_id": "t1", "description": "quick solo", "depends_on": []},
        {"task_id": "t2", "description": "flagged task", "depends_on": [],
         "requires_user_input": True},
        {"task_id": "t3", "description": "review pause", "depends_on": []},
        {"task_id": "t4", "description": "merge all",
         "depends_on": ["t1", "t2", "t3"]},
    ],
}


def is_requires(tid):
    return lambda f: f["type"] == "task_requires_input" and f["data"]["task_id"] == tid


# ---------------------------------------------------------------------------
# 2. End-to-end pause + resume (planner flag and reviewer flag)
# ---------------------------------------------------------------------------

def test_pause_and_resume():
    script = {
        "quick solo": [{"approved": True, "feedback": "ok"}],
        # First review parks t3 with a question; the post-feedback re-review passes.
        "review pause": [
            {"approved": False, "feedback": "Which tone should this take?",
             "requires_user_input": True},
            {"approved": True, "feedback": "tone fixed"},
        ],
        "flagged task": [{"approved": True, "feedback": "ok"}],
        "merge all": [{"approved": True, "feedback": "ok"}],
    }
    orchestrator, provider, ws = build_orch(PAUSE_PLAN, script)

    async def scenario():
        run = asyncio.create_task(orchestrator.run("pause demo goal", websocket=ws))

        # --- planner-flagged pause on t2 -----------------------------------
        frame = await ws.wait_for(is_requires("t2"))
        assert "needs your approval" in frame["data"]["question"]

        # Sibling work continues while t2 is parked: t1 finishes untouched.
        await ws.wait_for(lambda f:
                          f["type"] == "task_worker_completed" and f["data"]["task_id"] == "t1")
        t2_done = ws.has(lambda f: f["type"] == "task_worker_completed"
                         and f["data"]["task_id"] == "t2")
        assert t2_done is None, "t2 executed before user replied"
        assert not run.done(), "run must not die while a task is paused"

        assert orchestrator.submit_feedback("t2", "make it about lighthouses") is True
        assert orchestrator.submit_feedback("nope", "x") is False

        resumed = await ws.wait_for(lambda f:
                                    f["type"] == "task_resumed" and f["data"]["task_id"] == "t2")
        assert resumed["data"]["feedback"] == "make it about lighthouses"

        # --- reviewer-flagged pause on t3 ----------------------------------
        frame = await ws.wait_for(is_requires("t3"))
        assert frame["data"]["question"] == "Which tone should this take?"
        # t3's *first* worker output already exists (pause happened after review)
        assert len(provider.worker_prompts["review pause"]) == 1
        orchestrator.submit_feedback("t3", "Use a formal tone")

        result = await asyncio.wait_for(run, timeout=30)

        # --- feedback reached the right agent contexts ---------------------
        t2_prompts = provider.worker_prompts["flagged task"]
        assert len(t2_prompts) == 1, "t2 must run exactly once, after approval"
        assert "make it about lighthouses" in t2_prompts[0]
        t3_prompts = provider.worker_prompts["review pause"]
        assert len(t3_prompts) == 2, "t3 should re-run the worker after feedback"
        assert "Use a formal tone" in t3_prompts[1]
        # merge consumed the resumed dependency output
        assert "OUT[flagged task]" in provider.worker_prompts["merge all"][-1]

        # --- statuses + wire ------------------------------------------------
        by_id = {t["task_id"]: t for t in result["tasks"]}
        assert by_id["t2"]["status"] == "completed"
        assert by_id["t3"]["status"] == "approved_after_retry"
        assert by_id["t4"]["status"] == "completed"
        rc = ws.has(lambda f: f["type"] == "run_completed")
        assert rc and rc["data"]["final_output"] == "OUT[merge all]"
        assert not ws.has(lambda f: f["type"] == "task_input_timeout")
        return result

    result = asyncio.run(scenario())
    assert result["summary"]["completed_tasks"] == 4
    print("PASS  pause/resume: planner flag gated t2, reviewer flag parked t3 "
          "(siblings ran on, WS held open, feedback injected, wave resumed)")


# ---------------------------------------------------------------------------
# 3. Timeout path
# ---------------------------------------------------------------------------

def test_pause_timeout():
    plan = {
        "summary": "s", "execution_order": "dag",
        "tasks": [
            {"task_id": "t1", "description": "quick solo", "depends_on": []},
            {"task_id": "t2", "description": "flagged task", "depends_on": [],
             "requires_user_input": True},
            {"task_id": "t3", "description": "after flagged",
             "depends_on": ["t2"]},
        ],
    }
    script = {
        "quick solo": [{"approved": True, "feedback": "ok"}],
        "flagged task": [{"approved": True, "feedback": "ok"}],
        "after flagged": [{"approved": True, "feedback": "ok"}],
    }
    orchestrator, provider, ws = build_orch(plan, script)

    original = orch_main.FEEDBACK_TIMEOUT_SECONDS
    orch_main.FEEDBACK_TIMEOUT_SECONDS = 0.2
    try:
        async def scenario():
            run = asyncio.create_task(orchestrator.run("timeout goal", websocket=ws))
            await ws.wait_for(is_requires("t2"))
            # Nobody answers: t2 must time out and fail, run must continue.
            frame = await ws.wait_for(lambda f: f["type"] == "task_input_timeout")
            assert frame["data"]["task_id"] == "t2"
            result = await asyncio.wait_for(run, timeout=30)
            by_id = {t["task_id"]: t for t in result["tasks"]}
            assert by_id["t2"]["status"] == "failed"
            assert by_id["t1"]["status"] == "completed"
            # Dependent task still ran and was told the dependency produced nothing
            assert "dependency t2 produced no usable output" in \
                provider.worker_prompts["after flagged"][-1]
            assert provider.worker_prompts.get("flagged task") is None, \
                "timed-out task must never reach the worker"
            return result
        asyncio.run(scenario())
    finally:
        orch_main.FEEDBACK_TIMEOUT_SECONDS = original
    print("PASS  timeout: unanswered pause fails only that task; dependents learn "
          "the dependency produced no output")


if __name__ == "__main__":
    test_flag_parsing()
    test_pause_and_resume()
    test_pause_timeout()
    print("\nAll Milestone 9 tests passed.")
