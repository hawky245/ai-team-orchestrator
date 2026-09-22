"""Milestone 11 tests: task-level model routing + dynamic API-key injection.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_routing.py
"""

from __future__ import annotations

import asyncio
import json
import os
import types

os.environ["LLM_API_KEY"] = "env-key"

import openai

import src.providers.base_provider as bp
from src.agents.planner_agent import PlannerAgent
from src.agents.reviewer_agent import ReviewerAgent
from src.agents.worker_agent import WorkerAgent
from src.providers.base_provider import (
    ModelExhaustedError,
    NvidiaNimProvider,
    ProviderConfig,
    ProviderResponse,
)
from src.schemas.models import Task, WorkerResult

CFG = ProviderConfig(model_id="global/model", temperature=0.0, max_tokens=32)


def make_resp(content: str):
    class Msg:
        def __init__(self): self.content = content
    class Choice:
        def __init__(self):
            self.message = Msg()
            self.finish_reason = "stop"
    class Resp:
        def __init__(self):
            self.choices = [Choice()]
            self.usage = None
    return Resp()


def err_503():
    import httpx2 as httpx
    req = httpx.Request("POST", "https://example.invalid")
    return openai.APIStatusError("503 spike", response=httpx.Response(503, request=req), body=None)


class RecordingClient:
    """Fake openai client: records the `model` of every create() call."""

    def __init__(self, script=None):
        self.models: list[str] = []
        self.script = script or []  # per-call: Exception to raise, else success
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.models.append(kwargs.get("model"))
                n = len(outer.models) - 1
                if n < len(outer.script):
                    item = outer.script[n]
                    if isinstance(item, Exception):
                        raise item
                return make_resp('{"ok": true}')

        self.chat = type("C", (), {"completions": Completions()})()


def fresh_provider(**kw):
    p = NvidiaNimProvider(CFG, **kw)
    p.RETRY_BASE_DELAY = 0.001
    return p


# ---------------------------------------------------------------------------
# 1. Planner parses model_override
# ---------------------------------------------------------------------------

def test_planner_parses_model_override():
    planner = PlannerAgent.__new__(PlannerAgent)
    plan = planner._parse_plan(json.dumps({
        "summary": "s", "execution_order": "dag",
        "tasks": [
            {"task_id": "t1", "description": "a", "model_override": "meta/llama-70b"},
            {"task_id": "t2", "description": "b"},
            {"task_id": "t3", "description": "c", "model_override": "   "},
            {"task_id": "t4", "description": "d", "model_override": 42},
        ],
    }))
    assert plan.tasks[0].model_override == "meta/llama-70b"
    assert plan.tasks[1].model_override is None
    assert plan.tasks[2].model_override is None      # blank string rejected
    assert plan.tasks[3].model_override is None      # non-string rejected
    print("PASS  planner parses model_override (valid / missing / blank / wrong type)")


# ---------------------------------------------------------------------------
# 2. Provider honours the per-call model override
# ---------------------------------------------------------------------------

def test_provider_model_override():
    p = fresh_provider()
    rec = RecordingClient()
    p.api_keys = ["k"]
    p.client = rec

    async def go():
        await p.generate("sys", "user", schema={"type": "object"})
        await p.generate("sys", "user", schema={"type": "object"}, model="custom/model")
        await p.generate_with_tools("sys", "user", [], {}, model="tools/model")
        await p.generate_with_tools("sys", "user", [], {})
    asyncio.run(go())

    assert rec.models == ["global/model", "custom/model", "tools/model", "global/model"], rec.models

    # Fallback chain uses the override as primary
    os.environ["LLM_FALLBACK_MODEL_ID"] = "fb/model"
    try:
        p2 = fresh_provider()
        # All 3 primary attempts fail -> fallback model takes over and succeeds
        rec2 = RecordingClient(script=[err_503()] * 3)
        p2.api_keys = ["k"]
        p2.client = rec2
        asyncio.run(p2.generate("sys", "u", schema={}, model="custom/model"))
        assert rec2.models == ["custom/model"] * 3 + ["fb/model"], rec2.models

        # Exhaustion reports the overridden primary
        p3 = fresh_provider()
        rec3 = RecordingClient(script=[err_503()] * 5)
        p3.api_keys = ["k"]
        p3.client = rec3
        try:
            asyncio.run(p3.generate("sys", "u", schema={}, model="custom/model"))
            raise AssertionError("expected ModelExhaustedError")
        except ModelExhaustedError as e:
            assert e.primary_model == "custom/model"
            assert e.fallback_model == "fb/model"
    finally:
        os.environ.pop("LLM_FALLBACK_MODEL_ID", None)
    print("PASS  provider routes per-call model override (plain, tools, fallback, exhaustion)")


# ---------------------------------------------------------------------------
# 3. Dynamic API-key injection + rotation
# ---------------------------------------------------------------------------

def test_api_key_injection():
    # Default: environment key
    p_env = fresh_provider()
    assert p_env.api_keys == ["env-key"]
    assert getattr(p_env.client, "api_key") == "env-key"

    # Injected key wins over env, is trimmed, and comma-splits into rotation set
    p_user = fresh_provider(api_key="  user-a , user-b  ")
    assert p_user.api_keys == ["user-a", "user-b"]
    assert getattr(p_user.client, "api_key") == "user-a"

    # Empty/None injection falls back to env
    assert fresh_provider(api_key="   ").api_keys == ["env-key"]
    assert fresh_provider(api_key=None).api_keys == ["env-key"]

    # Rotation over the INJECTED keys (patch the client class generate()
    # rebuilds with, so no real network happens): first call rotates to
    # user-b, second back to user-a.
    created: list = []

    class RotatingClient(RecordingClient):
        def __init__(self, api_key=None, **kwargs):
            super().__init__()
            self.api_key = api_key
            created.append(self)

    original = bp.openai
    bp.openai = types.SimpleNamespace(
        OpenAI=RotatingClient,
        APITimeoutError=openai.APITimeoutError,
        APIConnectionError=openai.APIConnectionError,
        RateLimitError=openai.RateLimitError,
        APIStatusError=openai.APIStatusError,
    )
    try:
        p_rot = fresh_provider(api_key="user-a,user-b")
        asyncio.run(p_rot.generate("sys", "u", schema={}))
        asyncio.run(p_rot.generate("sys", "u", schema={}))
    finally:
        bp.openai = original

    # [0] = constructor client, then one rebuild per rotated request
    assert [c.api_key for c in created] == ["user-a", "user-b", "user-a"], created
    print("PASS  injected key overrides env, feeds rotation, empty falls back")


# ---------------------------------------------------------------------------
# 4. Worker + Reviewer forward task.model_override
# ---------------------------------------------------------------------------

class CapturingProvider:
    def __init__(self, with_tools: bool):
        self.calls: list[dict] = []
        if with_tools:
            self.generate_with_tools = self._tools  # presence routes the worker

    async def generate(self, system_prompt, user_prompt, schema=None, **kwargs):
        self.calls.append({"kind": "generate", "model": kwargs.get("model")})
        return ProviderResponse(content=self._content(system_prompt))

    async def _tools(self, system_prompt, user_prompt, tools, tool_registry,
                     on_tool_call=None, on_retry=None, model=None):
        self.calls.append({"kind": "tools", "model": model})
        return ProviderResponse(content=self._content(system_prompt))

    @staticmethod
    def _content(system_prompt: str) -> str:
        if "Reviewer" in system_prompt:
            return json.dumps({"approved": True, "feedback": "ok"})
        return json.dumps({"raw_output": "out", "artifacts": []})


def test_agents_forward_override():
    task = Task(task_id="t1", description="d", model_override="meta/special")
    plain = Task(task_id="t2", description="d")

    for with_tools in (False, True):
        prov = CapturingProvider(with_tools=with_tools)
        asyncio.run(WorkerAgent(prov).execute(task))
        asyncio.run(WorkerAgent(prov).execute(plain))
        asyncio.run(ReviewerAgent(prov).evaluate(task, WorkerResult(raw_output="x")))
        models = [(c["kind"], c["model"]) for c in prov.calls]
        expected = [
            ("tools" if with_tools else "generate", "meta/special"),
            ("tools" if with_tools else "generate", None),
            ("generate", "meta/special"),
        ]
        assert models == expected, (with_tools, models)
    print("PASS  worker (both call paths) and reviewer forward model_override")


# ---------------------------------------------------------------------------
# 5. Orchestrator constructor wiring
# ---------------------------------------------------------------------------

def test_orchestrator_wiring():
    import main as orch_main
    orch = orch_main.AI_TEAM_ORCHESTRATOR(api_key="dash-key-1,dash-key-2")
    assert orch.provider.api_keys == ["dash-key-1", "dash-key-2"]
    for agent in (orch.planner, orch.worker, orch.reviewer):
        assert agent.provider is orch.provider, "all agents must share the keyed provider"
    assert orch_main.AI_TEAM_ORCHESTRATOR().provider.api_keys == ["env-key"]
    print("PASS  orchestrator injects the key into the shared provider used by all agents")


if __name__ == "__main__":
    test_planner_parses_model_override()
    test_provider_model_override()
    test_api_key_injection()
    test_agents_forward_override()
    test_orchestrator_wiring()
    print("\nAll Milestone 11 tests passed.")
