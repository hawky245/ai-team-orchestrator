"""Milestone 13 tests: plan-review gate with per-task model assignment.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_plan_review.py
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

import main as orch_main
from tests.test_model_select import RecordingProvider, PLAN, SCRIPT
from tests.test_feedback import FakeWebSocket, FakeSession


def build():
    orch_main.SessionLocal = FakeSession
    provider = RecordingProvider()
    ws = FakeWebSocket()
    orchestrator = orch_main.AI_TEAM_ORCHESTRATOR()
    for agent in ("planner", "worker", "reviewer"):
        getattr(orchestrator, agent).provider = provider
    return orchestrator, provider, ws


def is_request(f):
    return f["type"] == "plan_review_requested"


def exec_models(provider):
    return {
        (kind, desc): model
        for kind, desc, model in provider.calls
        if kind in ("worker", "reviewer")
    }


# ---------------------------------------------------------------------------
# A. assignments applied per task; "auto" keeps the planner's pick
# ---------------------------------------------------------------------------

def test_assignment_applied():
    async def scenario():
        orch, prov, ws = build()
        # Nothing pending yet -> rejected
        assert orch.submit_plan_assignments({"t1": "early"}) is False

        run = asyncio.create_task(
            orch.run("review goal", websocket=ws, review_plan=True)
        )
        frame = await ws.wait_for(is_request)
        byid = {t["task_id"]: t for t in frame["data"]["tasks"]}
        # What each task does is visible in the review payload
        assert byid["t1"]["description"] == "one"
        assert byid["t1"]["depends_on"] == []
        assert byid["t3"]["depends_on"] == ["t1", "t2"]
        assert byid["t2"]["planner_model"] == "planner/pick"
        assert byid["t1"]["planner_model"] == "auto"

        # Held at the gate: no worker has run for ANY task yet
        assert [c for c in prov.calls if c[0] == "worker"] == []
        assert not run.done()

        assert orch.submit_plan_assignments(
            {"t1": "only/t1", "t2": "auto", "t3": "merge/t3"}
        ) is True
        result = await asyncio.wait_for(run, timeout=30)

        models = exec_models(prov)
        assert models[("worker", "one")] == "only/t1"
        assert models[("reviewer", "one")] == "only/t1"
        assert models[("worker", "two")] == "planner/pick"   # auto kept planner
        assert models[("worker", "three")] == "merge/t3"
        done = ws.has(lambda f: f["type"] == "plan_review_completed")
        assert done and done["data"]["assigned"] == 2
        assert result["summary"]["completed_tasks"] == 3
    asyncio.run(scenario())
    print("PASS  plan review: gate holds execution, task details exposed, per-task models applied, auto keeps planner pick")


# ---------------------------------------------------------------------------
# B. timeout degrades gracefully to planner routing
# ---------------------------------------------------------------------------

def test_review_timeout_continues():
    original = orch_main.FEEDBACK_TIMEOUT_SECONDS
    orch_main.FEEDBACK_TIMEOUT_SECONDS = 0.2
    try:
        async def scenario():
            orch, prov, ws = build()
            result = await orch.run(
                "review timeout goal", websocket=ws, review_plan=True
            )
            frame = ws.has(lambda f: f["type"] == "plan_review_timeout")
            assert frame, "expected plan_review_timeout"
            models = exec_models(prov)
            assert models[("worker", "two")] == "planner/pick"
            assert models[("worker", "one")] is None
            assert result["summary"]["completed_tasks"] == 3
        asyncio.run(scenario())
    finally:
        orch_main.FEEDBACK_TIMEOUT_SECONDS = original
    print("PASS  review timeout: run proceeds on planner routing instead of failing")


# ---------------------------------------------------------------------------
# C. precedence: per-task assignment beats the run-level forced selection
# ---------------------------------------------------------------------------

def test_assignment_beats_forced_selection():
    async def scenario():
        orch, prov, ws = build()
        run = asyncio.create_task(
            orch.run(
                "precedence goal",
                websocket=ws,
                model_selection="force/all",
                review_plan=True,
            )
        )
        await ws.wait_for(is_request)
        orch.submit_plan_assignments({"t2": "special/t2"})
        result = await asyncio.wait_for(run, timeout=30)
        models = exec_models(prov)
        # t1/t3 "auto" -> keeps the run-level force; t2's explicit choice wins
        assert models[("worker", "one")] == "force/all"
        assert models[("worker", "three")] == "force/all"
        assert models[("worker", "two")] == "special/t2"
        done = ws.has(lambda f: f["type"] == "plan_review_completed")
        assert done and done["data"]["assigned"] == 1
        assert result["summary"]["completed_tasks"] == 3
    asyncio.run(scenario())
    print("PASS  precedence: per-task assignment overrides run-level forced model")


# ---------------------------------------------------------------------------
# D. pre-selected slot models apply WITHOUT pausing
# ---------------------------------------------------------------------------

def test_preselection_no_pause():
    async def scenario():
        orch, prov, ws = build()
        result = await orch.run(
            "preselect goal",
            websocket=ws,
            plan_models={"t1": "pre/one", "t2": "auto", "t9": "unknown/slot"},
        )
        # No gate: nothing was ever requested
        assert ws.has(is_request) is None
        assert ws.has(lambda f: f["type"] == "plan_review_timeout") is None
        models = exec_models(prov)
        assert models[("worker", "one")] == "pre/one"
        assert models[("worker", "two")] == "planner/pick"   # auto keeps planner
        assert models[("worker", "three")] is None
        applied = ws.has(lambda f: f["type"] == "plan_models_applied")
        assert applied and applied["data"]["assigned"] == 1  # unknown t9 ignored
        assert result["summary"]["completed_tasks"] == 3
    asyncio.run(scenario())
    print("PASS  pre-selection: applies after planning without pausing; unknown slots ignored")


# ---------------------------------------------------------------------------
# E. precedence: run-level force first, pre-selection beats it per task
# ---------------------------------------------------------------------------

def test_preselection_beats_run_level_force():
    async def scenario():
        orch, prov, ws = build()
        await orch.run(
            "precedence goal",
            websocket=ws,
            model_selection="force/all",
            plan_models={"t2": "pre/two"},
        )
        models = exec_models(prov)
        assert models[("worker", "one")] == "force/all"
        assert models[("worker", "three")] == "force/all"
        assert models[("worker", "two")] == "pre/two"
    asyncio.run(scenario())
    print("PASS  precedence: pre-selected task beats run-level forced model")


if __name__ == "__main__":
    test_assignment_applied()
    test_review_timeout_continues()
    test_assignment_beats_forced_selection()
    test_preselection_no_pause()
    test_preselection_beats_run_level_force()
    print("\nAll Milestone 13 tests passed.")
