"""Milestone 8 tests: DAG wave planning, planner schema, concurrent
execution engine, and serialized (lock-protected) WebSocket emissions.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_parallel.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

import main as orch_main
from src.agents.planner_agent import PlannerAgent
from src.providers.base_provider import ProviderResponse
from src.schemas.models import Task


# ---------------------------------------------------------------------------
# 1. Wave planning
# ---------------------------------------------------------------------------

def T(tid, deps=(), par=False):
    return Task(
        task_id=tid,
        description=f"task {tid}",
        dependencies=list(deps),
        is_parallel=par,
    )


def wave_ids(waves):
    return [[t.task_id for t in w] for w in waves]


def test_wave_planning():
    # Diamond: t1 -> {t2, t3} -> t4
    waves = orch_main.plan_execution_waves(
        [T("t1"), T("t2", ["t1"]), T("t3", ["t1"]), T("t4", ["t2", "t3"])]
    )
    assert wave_ids(waves) == [["t1"], ["t2", "t3"], ["t4"]], waves

    # is_parallel overrides declared dependencies
    waves = orch_main.plan_execution_waves([T("t1"), T("t2", ["t1"], par=True)])
    assert wave_ids(waves) == [["t1", "t2"]], waves

    # Unknown / self dependencies never block
    waves = orch_main.plan_execution_waves([T("t1", ["zz"]), T("t2", ["t2"])])
    assert wave_ids(waves) == [["t1", "t2"]], waves

    # Cycle: unschedulable remainder runs together in a final wave (no deadlock)
    waves = orch_main.plan_execution_waves([T("t1", ["t2"]), T("t2", ["t1"]), T("t3")])
    assert wave_ids(waves) == [["t3"], ["t1", "t2"]], waves

    # Explicit sequential mode forces a one-task-per-wave chain
    waves = orch_main.plan_execution_waves(
        [T("t1"), T("t2"), T("t3")], execution_order="sequential"
    )
    assert wave_ids(waves) == [["t1"], ["t2"], ["t3"]], waves

    # Fully independent plan collapses into a single wide wave
    waves = orch_main.plan_execution_waves([T("t1"), T("t2"), T("t3")])
    assert wave_ids(waves) == [["t1", "t2", "t3"]], waves
    print("PASS  wave planning (diamond / is_parallel / bad deps / cycle / sequential / wide)")


# ---------------------------------------------------------------------------
# 2. Planner schema parsing: depends_on + is_parallel (+ legacy aliases)
# ---------------------------------------------------------------------------

def test_planner_parsing():
    planner = PlannerAgent.__new__(PlannerAgent)  # no provider needed for parsing

    plan = planner._parse_plan(json.dumps({
        "summary": "s",
        "execution_order": "dag",
        "tasks": [
            {"task_id": "t1", "description": "a", "depends_on": [], "is_parallel": True},
            {"task_id": "t2", "description": "b", "depends_on": ["t1"], "is_parallel": False},
            # legacy key + both keys merged/deduped; is_parallel optional
            {"task_id": "t3", "description": "c", "dependencies": ["t1"], "depends_on": ["t1", "t2"]},
        ],
    }))
    t1, t2, t3 = plan.tasks
    assert t1.is_parallel is True and t1.dependencies == []
    assert t2.is_parallel is False and t2.dependencies == ["t1"]
    assert t3.is_parallel is False and t3.dependencies == ["t1", "t2"]
    assert plan.execution_order == "dag"

    # Missing optional fields must not explode
    plan = planner._parse_plan(json.dumps({
        "summary": "s", "execution_order": "sequential",
        "tasks": [{"task_id": f"t{i}", "description": "d"} for i in (1, 2, 3)],
    }))
    assert all(t.dependencies == [] and t.is_parallel is False for t in plan.tasks)
    print("PASS  planner parsing (depends_on, is_parallel, alias merge, missing optionals)")


# ---------------------------------------------------------------------------
# Fakes for the end-to-end offline wire check
# ---------------------------------------------------------------------------

PLAN = {
    "summary": "three independent then one merge",
    "tasks": [
        {"task_id": "t1", "description": "independent one", "depends_on": [], "is_parallel": True, "context": {}},
        {"task_id": "t2", "description": "independent two", "depends_on": [], "is_parallel": True, "context": {}},
        {"task_id": "t3", "description": "independent three", "depends_on": [], "is_parallel": True, "context": {}},
        {"task_id": "t4", "description": "merge all", "depends_on": ["t1", "t2", "t3"], "context": {}},
    ],
    "execution_order": "dag",
}

WORKER_DELAY = 0.25


class FakeProvider:
    """Minimal stand-in for the LLM provider (no generate_with_tools)."""

    def __init__(self):
        self.worker_prompts: dict[str, str] = {}
        self.worker_calls = 0
        self.active_workers = 0
        self.max_active_workers = 0

    @staticmethod
    def _resp(content: str) -> ProviderResponse:
        return ProviderResponse(content=content, finish_reason="stop")

    async def generate(self, system_prompt, user_prompt, schema=None, **kwargs):
        if "Planner Agent" in system_prompt:
            return self._resp(json.dumps(PLAN))
        if "Reviewer Agent" in system_prompt:
            return self._resp(json.dumps({"approved": True, "feedback": "ok"}))
        # Worker call: hold "generation" open so genuine overlap is observable
        self.worker_calls += 1
        self.active_workers += 1
        self.max_active_workers = max(self.max_active_workers, self.active_workers)
        try:
            await asyncio.sleep(WORKER_DELAY)
        finally:
            self.active_workers -= 1
        marker = user_prompt.split("\n")[1]  # description line
        task_desc = marker.replace("Task Description:", "").strip()
        self.worker_prompts[task_desc] = user_prompt
        return self._resp(json.dumps({
            "raw_output": f"OUTPUT[{task_desc}]",
            "artifacts": [],
        }))


class FakeWebSocket:
    """Records frames; fails loudly if two sends ever overlap."""

    def __init__(self):
        self.frames: list[dict] = []
        self.timeline: list[tuple[float, str]] = []
        self.inflight = 0
        self.t0 = time.monotonic()

    async def send_text(self, payload):
        self.inflight += 1
        if self.inflight > 1:
            raise AssertionError("CONCURRENT WEBSOCKET SEND DETECTED (lock missing)")
        try:
            # Yield mid-send so competing coroutines CAN interleave if unlocked
            await asyncio.sleep(0.001)
            await asyncio.sleep(0.001)
            frame = json.loads(payload)
            assert "type" in frame, frame
            self.frames.append(frame)
            self.timeline.append((time.monotonic() - self.t0, frame["type"]))
        finally:
            self.inflight -= 1


class FakeSession:
    def add(self, obj):
        for attr in ("total_tasks", "completed_tasks", "rejected_tasks"):
            if hasattr(obj, attr) and getattr(obj, attr) is None:
                setattr(obj, attr, 0)

    def commit(self):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 3. End-to-end: real AI_TEAM_ORCHESTRATOR.run with fakes
# ---------------------------------------------------------------------------

def test_concurrent_run():
    orch_main.SessionLocal = FakeSession

    provider = FakeProvider()
    ws = FakeWebSocket()
    orchestrator = orch_main.AI_TEAM_ORCHESTRATOR()
    orchestrator.provider = provider
    orchestrator.planner.provider = provider
    orchestrator.worker.provider = provider
    orchestrator.reviewer.provider = provider

    t0 = time.monotonic()
    result = asyncio.run(orchestrator.run("build something", websocket=ws))
    wall = time.monotonic() - t0

    # --- scheduling evidence -------------------------------------------------
    assert provider.max_active_workers == 3, (
        f"expected 3 concurrent workers in wave 1, saw {provider.max_active_workers}"
    )
    sequential_floor = 4 * WORKER_DELAY
    assert wall < sequential_floor * 0.9, (
        f"run took {wall:.2f}s; sequential floor is {sequential_floor:.2f}s — not concurrent"
    )

    # task_started for all three independents must precede any of their completions
    all_task_started = [typ for _, typ in ws.timeline]
    assert all_task_started.count("task_started") == 4
    first_completion_pos = all_task_started.index("task_worker_completed")
    assert first_completion_pos >= 4, ws.timeline[:8]

    # dependent task strictly after its dependencies completed
    order = [typ for _, typ in ws.timeline]
    t4_start = next(
        i for i, f in enumerate(ws.frames)
        if f["type"] == "task_started" and f["data"]["task_id"] == "t4"
    )
    t4_after_deps = (
        max(
            i for i, f in enumerate(ws.frames)
            if f["type"] == "task_worker_completed" and f["data"]["task_id"] in ("t1", "t2", "t3")
        )
        < t4_start
    )
    assert t4_after_deps, "t4 started before its dependencies completed"

    # --- context injection is dependency-scoped, not append-order ------------
    # Wave-1 tasks must NOT see any outputs (none of their deps exist)
    assert "OUTPUT[" not in provider.worker_prompts["independent one"]
    merge_prompt = provider.worker_prompts["merge all"]
    for dep in ("OUTPUT[independent one]", "OUTPUT[independent two]", "OUTPUT[independent three]"):
        assert dep in merge_prompt, merge_prompt

    # --- wire format ----------------------------------------------------------
    types = {f["type"] for f in ws.frames}
    for expected in (
        "execution_started", "planning_completed", "task_started",
        "task_worker_completed", "task_review_passed", "run_completed",
        "execution_completed",
    ):
        assert expected in types, (expected, sorted(types))
    rc = next(f for f in ws.frames if f["type"] == "run_completed")
    assert rc["data"]["final_output"] == "OUTPUT[merge all]", rc
    assert all("data" not in f or isinstance(f["data"], dict) for f in ws.frames)

    assert result["summary"]["completed_tasks"] == 4
    print(
        f"PASS  concurrent run: {provider.max_active_workers} workers overlapped, "
        f"{wall:.2f}s wall vs {sequential_floor:.2f}s sequential floor, "
        f"{len(ws.frames)} serialized frames, DAG order + dep-context verified"
    )


if __name__ == "__main__":
    test_wave_planning()
    test_planner_parsing()
    test_concurrent_run()
    print("\nAll Milestone 8 tests passed.")
