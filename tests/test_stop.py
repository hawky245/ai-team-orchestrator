"""Milestone 21 tests: STOP button (request_stop) + pause context payload.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_stop.py
"""

from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

import main as orch_main
from src.providers.base_provider import ProviderResponse
from tests.test_feedback import FakeWebSocket, FakeSession

orch_main.compute_delay = lambda *a, **k: 0.0

PLAN = {
    "summary": "s", "execution_order": "dag",
    "tasks": [
        {"task_id": "t1", "description": "research", "depends_on": []},
        {"task_id": "t2", "description": "choose", "depends_on": ["t1"],
         "requires_user_input": True},
        {"task_id": "t3", "description": "write", "depends_on": ["t2"]},
    ],
}


class Provider:
    """Fake provider with deterministic hooks: `after_planner` and
    `after_review(n)` fire inside the run coroutine, so a stop request lands
    with zero scheduling gap (the old wait_for-then-stop pattern raced the
    wave boundary and flaked on slow CI runners)."""

    def __init__(self):
        self.worker_calls: list[str] = []
        self.after_planner = None
        self.review_count = 0
        self.after_review = None

    async def generate(self, system_prompt, user_prompt, schema=None, **kwargs):
        if "Planner Agent" in system_prompt:
            resp = ProviderResponse(content=json.dumps(PLAN))
            if self.after_planner:
                self.after_planner()
            return resp
        self.review_count += 1
        if self.after_review:
            self.after_review(self.review_count)
        return ProviderResponse(content=json.dumps(
            {"approved": True, "feedback": "ok"}))

    async def generate_with_tools(self, system_prompt, user_prompt, tools,
                                  tool_registry, on_tool_call=None,
                                  on_retry=None, model=None):
        desc = user_prompt.split("\n")[1].replace("Task Description:", "").strip()
        self.worker_calls.append(desc)
        return ProviderResponse(content=json.dumps(
            {"raw_output": f"OUT[{desc}]"}))


def build():
    orch_main.SessionLocal = FakeSession
    prov = Provider()
    ws = FakeWebSocket()
    orch = orch_main.AI_TEAM_ORCHESTRATOR()
    for agent in ("planner", "worker", "reviewer"):
        getattr(orch, agent).provider = prov
    return orch, prov, ws


# ---------------------------------------------------------------------------
# A. A paused task's frame carries the upstream outputs (the real options).
# ---------------------------------------------------------------------------

def test_pause_carries_context():
    async def scenario():
        orch, prov, ws = build()
        run = asyncio.create_task(orch.run("context goal", websocket=ws))
        frame = await ws.wait_for(lambda f: f["type"] == "task_requires_input")
        ctx = frame["data"]["context"]
        assert "t1" in ctx and "OUT[research]" in ctx["t1"], ctx
        orch.submit_feedback("t2", "pick vLLM")
        result = await asyncio.wait_for(run, timeout=30)
        assert result["summary"]["completed_tasks"] == 3, result
    asyncio.run(scenario())
    print("PASS  task_requires_input carries upstream dependency outputs")


# ---------------------------------------------------------------------------
# B. request_stop() halts before the next wave and reports it on the wire.
# ---------------------------------------------------------------------------

def test_request_stop_halts_run():
    async def scenario():
        orch, prov, ws = build()
        run = asyncio.create_task(orch.run("stop goal", websocket=ws))
        # Let wave 1 (t1) finish and the t2 pause appear, then approve t2 so
        # wave 2 (t3) is still pending when the stop lands.
        frame = await ws.wait_for(lambda f: f["type"] == "task_requires_input")
        assert frame["data"]["task_id"] == "t2"
        # Stop from inside t2's review call (same coroutine as the pipeline):
        # wave 3's boundary check cannot pass before the flag is set.
        prov.after_review = lambda n: n >= 2 and orch.request_stop()
        orch.submit_feedback("t2", "pick vLLM")
        result = await asyncio.wait_for(run, timeout=30)
        assert result == {"error": "stopped by user", "status": "stopped"}, result
        failed = ws.has(lambda f: f["type"] == "execution_failed")
        assert failed and "stopped by user" in failed["data"]["error"], failed
        assert "write" not in prov.worker_calls, prov.worker_calls  # t3 never ran
    asyncio.run(scenario())
    print("PASS  request_stop halts at the wave boundary and emits execution_failed")


# ---------------------------------------------------------------------------
# C. Stop before any task: per-task gate skips everything.
# ---------------------------------------------------------------------------

def test_stop_skips_unstarted_tasks():
    async def scenario():
        orch, prov, ws = build()
        # Stop from inside the planner call: the wave-1 boundary check runs
        # after it, so no task can start.
        prov.after_planner = orch.request_stop
        run = asyncio.create_task(orch.run("pre-stop goal", websocket=ws))
        result = await asyncio.wait_for(run, timeout=30)
        assert result["status"] == "stopped", result
        assert prov.worker_calls == [], prov.worker_calls
    asyncio.run(scenario())
    print("PASS  stopping right after planning skips every task")


if __name__ == "__main__":
    test_pause_carries_context()
    test_request_stop_halts_run()
    test_stop_skips_unstarted_tasks()
    print("\nAll Milestone 21 stop/context tests passed.")
