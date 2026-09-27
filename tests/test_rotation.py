"""Milestone 17 tests: step-level model rotation on failure.

When a task permanently fails on its assigned model (worker errors out or
the reviewer rejects), the SAME step must be re-run on the next model from
the availability list, emitting `task_model_retry`, until it passes or the
rotation budget is spent.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_rotation.py
"""

from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

import main as orch_main
from src.providers.base_provider import ProviderError, ProviderResponse
from tests.test_feedback import FakeWebSocket, FakeSession
from tests.test_model_select import PLAN

# Kill the backoff sleeps for test speed.
orch_main.compute_delay = lambda *a, **k: 0.0


class RotProvider:
    """Model-aware fake: worker dies on fail_worker models, returns EMPTY
    output on empty_on models, the reviewer rejects on reject_models."""

    def __init__(self, fail_worker=(), reject_models=(), empty_on=()):
        self.fail_worker = set(fail_worker)
        self.reject_models = set(reject_models)
        self.empty_on = set(empty_on)
        self.calls: list[tuple[str, str | None]] = []

    async def generate(self, system_prompt, user_prompt, schema=None, **kwargs):
        model = kwargs.get("model")
        if "Planner Agent" in system_prompt:
            self.calls.append(("planner", model))
            return ProviderResponse(content=json.dumps(PLAN))
        if "Reviewer" in system_prompt:
            self.calls.append(("reviewer", model))
            ok = model not in self.reject_models
            return ProviderResponse(content=json.dumps(
                {"approved": ok,
                 "feedback": "fine" if ok else "not good enough",
                 "retry_allowed": False}
            ))
        self.calls.append(("worker", model))
        return ProviderResponse(content=json.dumps({"raw_output": "OUT", "artifacts": []}))

    async def generate_with_tools(self, system_prompt, user_prompt, tools,
                                  tool_registry, on_tool_call=None,
                                  on_retry=None, model=None):
        self.calls.append(("worker", model))
        if model in self.fail_worker:
            raise ProviderError(f"model {model} is dead")
        if model in self.empty_on:
            return ProviderResponse(content=json.dumps({"raw_output": "  ", "artifacts": []}))
        return ProviderResponse(content=json.dumps({"raw_output": "OUT", "artifacts": []}))


def build(provider):
    orch_main.SessionLocal = FakeSession
    ws = FakeWebSocket()
    orchestrator = orch_main.AI_TEAM_ORCHESTRATOR()
    for agent in ("planner", "worker", "reviewer"):
        getattr(orchestrator, agent).provider = provider
    return orchestrator, ws


def rotations(ws):
    return [f for f in ws.frames if f["type"] == "task_model_retry"]


# ---------------------------------------------------------------------------
# A. Worker hard-fails on the assigned model -> step repeats on the next one
# ---------------------------------------------------------------------------

def test_worker_failure_rotates():
    prov = RotProvider(fail_worker=["fail/a"])
    orch, ws = build(prov)
    result = asyncio.run(orch.run(
        "rotate worker goal", websocket=ws,
        model_selection="fail/a",
        available_models=["fail/a", "pass/b"],
    ))
    rots = rotations(ws)
    assert len(rots) == 3, rots                      # one per task
    assert all(r["data"]["from_model"] == "fail/a" for r in rots)
    assert all(r["data"]["to_model"] == "pass/b" for r in rots)
    assert result["summary"]["completed_tasks"] == 3, result["summary"]
    # 3 provider attempts burned on fail/a before each rotation
    dead = [m for kind, m in prov.calls if kind == "worker" and m == "fail/a"]
    assert len(dead) == 9, len(dead)
    print("PASS  worker exhaustion rotates the step to the next model (3 attempts per model)")


# ---------------------------------------------------------------------------
# B. Reviewer REJECTS on the assigned model -> step repeats elsewhere
# ---------------------------------------------------------------------------

def test_rejection_rotates():
    prov = RotProvider(reject_models=["rej/x"])
    orch, ws = build(prov)
    result = asyncio.run(orch.run(
        "rotate review goal", websocket=ws,
        model_selection="rej/x",
        available_models=["rej/x", "pass/b"],
    ))
    rots = rotations(ws)
    assert len(rots) == 3, rots
    assert all("rejected" in r["data"]["reason"] for r in rots), rots
    assert all(r["data"]["to_model"] == "pass/b" for r in rots)
    assert result["summary"]["completed_tasks"] == 3, result["summary"]
    assert result["summary"]["rejected_tasks"] == 0
    print("PASS  reviewer rejection repeats the step on the next model")


# ---------------------------------------------------------------------------
# C. Rotation budget spent -> task lands as rejected, no infinite loop
# ---------------------------------------------------------------------------

def test_rotation_budget_exhausts():
    prov = RotProvider(reject_models=["rej/x", "rej/y"])
    orch, ws = build(prov)
    result = asyncio.run(orch.run(
        "all reject goal", websocket=ws,
        model_selection="rej/x",
        available_models=["rej/x", "rej/y"],
    ))
    rots = rotations(ws)
    assert len(rots) == 3, rots                      # exactly one hop per task
    assert result["summary"]["rejected_tasks"] == 3, result["summary"]
    rejected = [f for f in ws.frames if f["type"] == "task_review_failed"]
    assert len(rejected) == 6, len(rejected)          # two models x three tasks
    print("PASS  exhausted rotation budget ends in REJECTED without looping")


# ---------------------------------------------------------------------------
# D. Rotation candidates are capability-ranked: an audio model in the list
#    is skipped in favour of a chat-capable one.
# ---------------------------------------------------------------------------

def test_capability_ranked_rotation():
    prov = RotProvider(fail_worker=["start/m0"])
    orch, ws = build(prov)
    result = asyncio.run(orch.run(
        "ranked rotate goal", websocket=ws,
        model_selection="start/m0",
        available_models=["start/m0", "a/orpheus-tts", "b/llama-next"],
    ))
    rots = rotations(ws)
    assert rots, "expected at least one rotation"
    # Second candidate must be the llama (chat-capable), not the TTS model
    assert all(r["data"]["to_model"] == "b/llama-next" for r in rots), \
        [r["data"]["to_model"] for r in rots]
    assert result["summary"]["completed_tasks"] == 3, result["summary"]
    print("PASS  rotation skips audio/TTS candidates and prefers chat-capable models")


# ---------------------------------------------------------------------------
# E. Empty raw_output is a FAILED attempt, not a completed task — it must
#    drive the same retry/rotation path (live-run bug: gpt-oss models kept
#    "completing" with nothing, then the reviewer rejected forever).
# ---------------------------------------------------------------------------

def test_empty_output_rotates():
    prov = RotProvider(empty_on=["start/m0"])
    orch, ws = build(prov)
    result = asyncio.run(orch.run(
        "empty output goal", websocket=ws,
        model_selection="start/m0",
        available_models=["start/m0", "b/llama-next"],
    ))
    rots = rotations(ws)
    assert len(rots) == 3, rots
    assert all("empty raw_output" in r["data"]["reason"] for r in rots), \
        [r["data"]["reason"] for r in rots]
    assert result["summary"]["completed_tasks"] == 3, result["summary"]
    print("PASS  empty raw_output counts as a failed attempt and rotates models")


if __name__ == "__main__":
    test_worker_failure_rotates()
    test_rejection_rotates()
    test_rotation_budget_exhausts()
    test_capability_ranked_rotation()
    test_empty_output_rotates()
    print("\nAll Milestone 17 rotation tests passed.")
