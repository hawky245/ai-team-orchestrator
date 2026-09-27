"""Milestone 16 tests: constrain planner routing to fetched available models.

Covers: the system-prompt whitelist, the planner call's own routing under the
whitelist, and the pre-wave validation guardrail (invalid picks corrected to
the primary default with a `model_guardrail` event).

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_guardrail.py
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

import main as orch_main
from src.agents.planner_agent import PlannerAgent
from tests.test_model_select import RecordingProvider, PLAN
from tests.test_feedback import FakeWebSocket, FakeSession


def build():
    orch_main.SessionLocal = FakeSession
    provider = RecordingProvider()
    ws = FakeWebSocket()
    orchestrator = orch_main.AI_TEAM_ORCHESTRATOR()
    for agent in ("planner", "worker", "reviewer"):
        getattr(orchestrator, agent).provider = provider
    env_model = orchestrator.provider.config.model_id
    return orchestrator, provider, ws, env_model


def exec_models(provider):
    return {
        (kind, desc): model
        for kind, desc, model in provider.calls
        if kind in ("worker", "reviewer")
    }


# ---------------------------------------------------------------------------
# A. The prompt constraint itself (pure unit, no run)
# ---------------------------------------------------------------------------

def test_prompt_constraint():
    planner = PlannerAgent.__new__(PlannerAgent)
    # No whitelist -> the stock system prompt, untouched.
    assert planner._system_prompt(None) == PlannerAgent.SYSTEM_PROMPT
    assert planner._system_prompt([]) == PlannerAgent.SYSTEM_PROMPT

    # Whitelist -> strict rule + the list verbatim.
    sp = planner._system_prompt(["groq/llama", "nv/other"])
    assert sp.startswith(PlannerAgent.SYSTEM_PROMPT)
    assert "only allowed to assign models from the following list" in sp
    assert '"groq/llama"' in sp and '"nv/other"' in sp
    assert "Do not assign any model outside this list under any circumstances" in sp

    # Oversized catalogues are capped IN THE PROMPT (validation still uses
    # the full list) so a 400-model bay cannot blow up the planning call.
    big = [f"p/m{i}" for i in range(PlannerAgent.PROMPT_MODEL_CAP + 25)]
    sp_big = planner._system_prompt(big)
    assert "truncated" in sp_big
    assert f"first {PlannerAgent.PROMPT_MODEL_CAP} of {len(big)}" in sp_big
    assert '"p/m79"' in sp_big and '"p/m80"' not in sp_big
    print("PASS  planner system-prompt whitelist (off / on / capped)")


# ---------------------------------------------------------------------------
# B. Planner picks outside the list -> corrected to the primary default,
#    tasks left on "auto" pinned too, model_guardrail event emitted, and the
#    planning CALL itself routed onto a servable model.
# ---------------------------------------------------------------------------

def test_invalid_picks_corrected():
    async def scenario():
        orch, prov, ws, env_model = build()
        avail = ["ok/first", "ok/second"]          # neither env nor planner/pick
        result = await orch.run(
            "guardrail goal", websocket=ws, available_models=avail
        )
        # The planning call itself must NOT die on the unservable env model.
        assert prov.calls[0] == ("planner", None, "ok/first"), prov.calls[0]
        frame = ws.has(lambda f: f["type"] == "model_guardrail")
        assert frame, "expected model_guardrail"
        assert frame["data"]["fallback_model"] == "ok/first"
        fixed = {c["task_id"]: c for c in frame["data"]["corrected"]}
        assert fixed["t2"]["from"] == "planner/pick" and fixed["t2"]["to"] == "ok/first"
        assert fixed["t1"]["from"] == "auto" and fixed["t3"]["from"] == "auto"
        models = exec_models(prov)
        assert all(m == "ok/first" for m in models.values()), models
        assert result["summary"]["completed_tasks"] == 3
    asyncio.run(scenario())
    print("PASS  invalid/auto picks corrected to primary default + event, planner call servable")


# ---------------------------------------------------------------------------
# C. Everything valid -> guardrail fully silent (no event, no rewrites)
# ---------------------------------------------------------------------------

def test_valid_list_untouched():
    async def scenario():
        orch, prov, ws, env_model = build()
        avail = [env_model, "planner/pick"]
        await orch.run("clean goal", websocket=ws, available_models=avail)
        assert ws.has(lambda f: f["type"] == "model_guardrail") is None
        assert prov.calls[0] == ("planner", None, env_model)
        models = exec_models(prov)
        assert models[("worker", "two")] == "planner/pick"
        assert models[("worker", "one")] is None      # auto stays on default
    asyncio.run(scenario())
    print("PASS  valid catalogue leaves routing untouched and emits no guardrail event")


# ---------------------------------------------------------------------------
# D. A run-level forced model outside the list is corrected too
# ---------------------------------------------------------------------------

def test_forced_outside_list():
    async def scenario():
        orch, prov, ws, env_model = build()
        avail = [env_model, "planner/pick"]
        await orch.run(
            "forced bogus", websocket=ws,
            model_selection="bogus/model", available_models=avail,
        )
        # planner routed to the servable default, not the bogus force
        assert prov.calls[0] == ("planner", None, env_model), prov.calls[0]
        frame = ws.has(lambda f: f["type"] == "model_guardrail")
        assert frame and frame["data"]["fallback_model"] == env_model
        assert all(c["from"] == "bogus/model" for c in frame["data"]["corrected"])
        models = exec_models(prov)
        assert all(m == env_model for m in models.values()), models
    asyncio.run(scenario())
    print("PASS  forced model outside the whitelist corrected across all tasks")


# ---------------------------------------------------------------------------
# E. Pre-selected slot models (plan_models) obey the same guardrail
# ---------------------------------------------------------------------------

def test_preselect_corrected():
    async def scenario():
        orch, prov, ws, env_model = build()
        avail = [env_model, "planner/pick"]
        await orch.run(
            "preselect goal", websocket=ws,
            plan_models={"t1": "ghost/model", "t3": "auto"},
            available_models=avail,
        )
        frame = ws.has(lambda f: f["type"] == "model_guardrail")
        assert frame, "expected model_guardrail for ghost preselect"
        fixed = {c["task_id"] for c in frame["data"]["corrected"]}
        assert fixed == {"t1"}                        # only the ghost pick
        models = exec_models(prov)
        assert models[("worker", "one")] == env_model
        assert models[("worker", "two")] == "planner/pick"
    asyncio.run(scenario())
    print("PASS  pre-selected slot models validated by the same guardrail")


# ---------------------------------------------------------------------------
# F. No whitelist (old clients) -> behavior identical to M15
# ---------------------------------------------------------------------------

def test_no_list_no_change():
    async def scenario():
        orch, prov, ws, env_model = build()
        await orch.run("legacy goal", websocket=ws)   # available_models omitted
        assert ws.has(lambda f: f["type"] == "model_guardrail") is None
        assert prov.calls[0] == ("planner", None, None)
        models = exec_models(prov)
        assert models[("worker", "two")] == "planner/pick"
    asyncio.run(scenario())
    print("PASS  absent whitelist preserves pre-M16 routing exactly")


# ---------------------------------------------------------------------------
# G. The guardrail default is capability-ranked: alphabetical catalogues
#    start with whisper/orpheus — the fallback must be a chat-capable model.
# ---------------------------------------------------------------------------

def test_capability_ranked_default():
    async def scenario():
        orch, prov, ws, env_model = build()
        avail = ["a/whisper-large", "b/orpheus-tts", "c/meta-llama-chat"]
        await orch.run("ranked default goal", websocket=ws, available_models=avail)
        # Planner call routed to the chat-capable entry, not avail[0]
        assert prov.calls[0] == ("planner", None, "c/meta-llama-chat"), prov.calls[0]
        frame = ws.has(lambda f: f["type"] == "model_guardrail")
        assert frame and frame["data"]["fallback_model"] == "c/meta-llama-chat"
    asyncio.run(scenario())
    print("PASS  guardrail default + planner routing prefer chat-capable models over alphabetical")


if __name__ == "__main__":
    test_prompt_constraint()
    test_invalid_picks_corrected()
    test_valid_list_untouched()
    test_forced_outside_list()
    test_preselect_corrected()
    test_no_list_no_change()
    test_capability_ranked_default()
    print("\nAll Milestone 16 tests passed.")
