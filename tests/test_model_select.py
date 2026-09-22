"""Milestone 12 tests: provider model listing + dashboard model-selection intercept.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_model_select.py
"""

from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

import openai

import main as orch_main
from src.providers.base_provider import (
    NvidiaNimProvider,
    ProviderConfig,
    ProviderError,
)
from tests.test_feedback import FakeProvider as ScriptedFakeProvider, FakeWebSocket, FakeSession

CFG = ProviderConfig(model_id="global/model", temperature=0.0, max_tokens=32)


def _resp_401():
    import httpx2 as httpx
    req = httpx.Request("GET", "https://example.invalid/models")
    return httpx.Response(401, request=req)


# ---------------------------------------------------------------------------
# 1. Provider.list_models
# ---------------------------------------------------------------------------

def test_list_models():
    class Model:
        def __init__(self, mid): self.id = mid

    class ModelsAPI:
        def __init__(self, data=None, error=None):
            self.data, self.error = data, error
        def list(self):
            if self.error:
                raise self.error
            return type("R", (), {"data": self.data})()

    def provider_with(models_api):
        p = NvidiaNimProvider(CFG, api_key="sk-secret-xyz")
        p.client = type("C", (), {"models": models_api})()
        return p

    # sorted + deduped ids
    p = provider_with(ModelsAPI(data=[Model("b/mod"), Model("a/mod"), Model("b/mod")]))
    assert asyncio.run(p.list_models()) == ["a/mod", "b/mod"]

    # empty catalogue
    assert asyncio.run(provider_with(ModelsAPI(data=[])).list_models()) == []
    assert asyncio.run(provider_with(ModelsAPI(data=None)).list_models()) == []

    # 401 -> clean ProviderError that does NOT echo the API key
    p_bad = provider_with(ModelsAPI(error=openai.AuthenticationError(
        "Unauthorized", response=_resp_401(), body=None)))
    try:
        asyncio.run(p_bad.list_models())
        raise AssertionError("expected ProviderError")
    except ProviderError as e:
        assert "401" in str(e)
        assert "sk-secret-xyz" not in str(e), "error must not echo the API key"
    print("PASS  list_models: sorted/deduped, empty-safe, 401 handled without leaking key")


# ---------------------------------------------------------------------------
# 2. Model-selection intercept in the execution loop
# ---------------------------------------------------------------------------

PLAN = {
    "summary": "s", "execution_order": "dag",
    "tasks": [
        {"task_id": "t1", "description": "one", "depends_on": []},
        {"task_id": "t2", "description": "two", "depends_on": [], "model_override": "planner/pick"},
        {"task_id": "t3", "description": "three", "depends_on": ["t1", "t2"]},
    ],
}
SCRIPT = {d: [{"approved": True, "feedback": "ok"}] for d in ("one", "two", "three")}


class RecordingProvider(ScriptedFakeProvider):
    def __init__(self):
        super().__init__(PLAN, SCRIPT)
        self.calls: list[tuple[str, str | None, str | None]] = []  # (kind, desc, model)

    async def generate(self, system_prompt, user_prompt, schema=None, **kwargs):
        model = kwargs.get("model")
        if "Planner Agent" in system_prompt:
            self.calls.append(("planner", None, model))
        elif "Reviewer Agent" in system_prompt:
            desc = next(d for d in self.reviewer_script if d in user_prompt.split("Worker Output")[0])
            self.calls.append(("reviewer", desc, model))
        else:
            desc = user_prompt.split("\n")[1].replace("Task Description:", "").strip()
            self.calls.append(("worker", desc, model))
        return await super().generate(system_prompt, user_prompt, schema=schema, **kwargs)


def run_with_selection(selection):
    orch_main.SessionLocal = FakeSession
    provider = RecordingProvider()
    ws = FakeWebSocket()
    orchestrator = orch_main.AI_TEAM_ORCHESTRATOR()
    for agent in ("planner", "worker", "reviewer"):
        getattr(orchestrator, agent).provider = provider
    result = asyncio.run(orchestrator.run(
        "selection goal", websocket=ws, model_selection=selection
    ))
    return provider, ws, result


def test_forced_selection():
    provider, ws, result = run_with_selection("meta/forced")
    assert provider.calls[0] == ("planner", None, None), \
        "the planner itself must stay on the global model"
    for kind, desc, model in provider.calls[1:]:
        assert model == "meta/forced", (kind, desc, model)
    # it also beats the planner's own t2 override
    t2_worker = [c for c in provider.calls if c[0] == "worker" and c[1] == "two"]
    assert t2_worker and all(m == "meta/forced" for _, _, m in t2_worker)
    frame = ws.has(lambda f: f["type"] == "model_selection")
    assert frame and frame["data"]["model"] == "meta/forced"
    assert frame["data"]["task_count"] == 3
    assert result["summary"]["completed_tasks"] == 3
    print("PASS  forced selection overrides every task (incl. planner's own override); planner call untouched")


def test_auto_selection_respects_planner():
    for selection in (None, "", "auto", "Auto"):
        provider, ws, _ = run_with_selection(selection)
        models = {(kind, desc): model for kind, desc, model in provider.calls if kind != "planner"}
        assert models[("worker", "two")] == "planner/pick", selection
        assert models[("reviewer", "two")] == "planner/pick", selection
        assert models[("worker", "one")] is None, selection
        assert models[("worker", "three")] is None, selection
        assert ws.has(lambda f: f["type"] == "model_selection") is None, selection
    print("PASS  auto/empty selections leave planner routing untouched (no event emitted)")


if __name__ == "__main__":
    test_list_models()
    test_forced_selection()
    test_auto_selection_respects_planner()
    print("\nAll Milestone 12 tests passed.")
