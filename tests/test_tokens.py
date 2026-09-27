"""Milestone 22 tests: token telemetry from provider to wire.

- provider.generate / generate_with_tools surface OpenAI-style usage counts,
  accumulating across tool-loop rounds;
- the orchestrator stamps prompt_tokens/completion_tokens onto per-task
  events and reports the run total in the run_completed summary.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_tokens.py
"""

from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

import main as orch_main
from src.providers.base_provider import NvidiaNimProvider, ProviderConfig, ProviderResponse
from tests.test_feedback import FakeWebSocket, FakeSession
from tests.test_model_select import PLAN

orch_main.compute_delay = lambda *a, **k: 0.0


# ---------------------------------------------------------------------------
# Fake OpenAI layer (same shape as tests/test_tool_loop.py)
# ---------------------------------------------------------------------------

def make_msg(content=None, tool_calls=None):
    m = type("M", (), {})()
    m.content = content
    m.tool_calls = tool_calls
    m.role = "assistant"
    if tool_calls:
        m.function_calls = None
    return m


def make_tool_call(id_, name, arguments):
    fn = type("Fn", (), {"name": name, "arguments": arguments})()
    return type("Tc", (), {"id": id_, "function": fn, "type": "function"})()


def make_resp(msg, usage=None):
    r = type("R", (), {})()
    r.choices = [type("C", (), {"message": msg, "finish_reason": "stop"})()]
    r.usage = (
        type("U", (), {"prompt_tokens": usage[0], "completion_tokens": usage[1]})()
        if usage else None
    )
    return r


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        return self.script.pop(0)


def provider():
    cfg = ProviderConfig(model_id="m", temperature=0.2, max_tokens=512)
    return NvidiaNimProvider(cfg)


# ---------------------------------------------------------------------------
# A. Plain generate surfaces usage; missing usage degrades to 0
# ---------------------------------------------------------------------------

def test_generate_extracts_usage():
    p = provider()
    p.client = FakeClient([make_resp(make_msg(content="hello"), usage=(11, 7))])
    r = asyncio.run(p.generate("sys", "user"))
    assert (r.prompt_tokens, r.completion_tokens) == (11, 7), r

    p.client = FakeClient([make_resp(make_msg(content="hello"), usage=None)])
    r = asyncio.run(p.generate("sys", "user"))
    assert (r.prompt_tokens, r.completion_tokens) == (0, 0), r
    print("PASS  generate() extracts usage, absent usage -> zeros")


# ---------------------------------------------------------------------------
# B. Tool loop accumulates tokens across rounds
# ---------------------------------------------------------------------------

def test_tool_loop_accumulates():
    from src.tools.worker_tools import WORKER_TOOLS, TOOL_REGISTRY

    p = provider()
    tool_msg = make_msg(tool_calls=[make_tool_call("c1", "web_search", json.dumps({"query": "x"}))])
    final_msg = make_msg(content='{"raw_output": "done"}')

    registry = dict(TOOL_REGISTRY, web_search=lambda query: json.dumps(
        {"status": "ok", "query": query, "results": []}))
    p.client = FakeClient([make_resp(tool_msg, usage=(10, 5)), make_resp(final_msg, usage=(20, 7))])
    r = asyncio.run(p.generate_with_tools("sys", "user", WORKER_TOOLS, registry, None, None))
    assert (r.prompt_tokens, r.completion_tokens) == (30, 12), r
    print("PASS  tool-loop totals accumulate across rounds (10+5, 20+7 -> 30/12)")


# ---------------------------------------------------------------------------
# C. Orchestrator: per-task events + run total carry the agent token counts
# ---------------------------------------------------------------------------

class TokProvider:
    async def generate(self, system_prompt, user_prompt, schema=None, **kwargs):
        if "Planner Agent" in system_prompt:
            return ProviderResponse(content=json.dumps(PLAN), prompt_tokens=100, completion_tokens=10)
        if "Reviewer" in system_prompt:
            return ProviderResponse(
                content=json.dumps({"approved": True, "feedback": "fine", "retry_allowed": False}),
                prompt_tokens=30, completion_tokens=3)
        return ProviderResponse(content="unused", prompt_tokens=1, completion_tokens=1)

    async def generate_with_tools(self, system_prompt, user_prompt, tools,
                                  tool_registry, on_tool_call=None,
                                  on_retry=None, model=None):
        return ProviderResponse(content=json.dumps({"raw_output": "OUT"}),
                                prompt_tokens=50, completion_tokens=5)


def test_pipeline_token_events():
    orch_main.SessionLocal = FakeSession
    ws = FakeWebSocket()
    orchestrator = orch_main.AI_TEAM_ORCHESTRATOR()
    for agent in ("planner", "worker", "reviewer"):
        getattr(orchestrator, agent).provider = TokProvider()

    result = asyncio.run(orchestrator.run(
        "token telemetry goal", websocket=ws,
        available_models=["test/model"],
    ))
    workers = [f for f in ws.frames if f["type"] == "task_worker_completed"]
    reviews = [f for f in ws.frames if f["type"] == "task_review_passed"]
    assert len(workers) == 3 and len(reviews) == 3, (len(workers), len(reviews))
    for f in workers:
        assert f["data"]["prompt_tokens"] == 50 and f["data"]["completion_tokens"] == 5, f
    for f in reviews:
        assert f["data"]["prompt_tokens"] == 30 and f["data"]["completion_tokens"] == 3, f

    tot = result["summary"]["total_tokens"]
    # 3 tasks x (worker 50/5 + reviewer 30/3); the planner call is not per-task.
    assert tot == {"prompt": 240, "completion": 24}, tot
    print("PASS  task events carry tokens; run_completed summary totals them (240/24)")


if __name__ == "__main__":
    test_generate_extracts_usage()
    test_tool_loop_accumulates()
    test_pipeline_token_events()
    print("\nAll token tests passed.")
