"""Milestone 24 tests: planner-assigned role names (data-driven, not hardcoded).

The role is chosen by the planner per goal and must flow from the parsed plan
through to the graph payload the dashboard renders.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_roles.py
"""

from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

import main as orch_main
from src.agents.planner_agent import PlannerAgent
from src.providers.base_provider import ProviderResponse
from tests.test_feedback import FakeWebSocket, FakeSession

orch_main.compute_delay = lambda *a, **k: 0.0

ROLED_PLAN = {
    "summary": "s", "execution_order": "dag",
    "tasks": [
        {"task_id": "t1", "role": "Researcher", "description": "gather", "depends_on": []},
        {"task_id": "t2", "role": "Analyst", "description": "assess", "depends_on": ["t1"]},
        {"task_id": "t3", "role": "Critic", "description": "review", "depends_on": ["t2"]},
    ],
}


class RoleProvider:
    async def generate(self, system_prompt, user_prompt, schema=None, **kwargs):
        if "Planner Agent" in system_prompt:
            return ProviderResponse(content=json.dumps(ROLED_PLAN))
        return ProviderResponse(content=json.dumps(
            {"approved": True, "feedback": "ok"}))

    async def generate_with_tools(self, system_prompt, user_prompt, tools,
                                  tool_registry, on_tool_call=None,
                                  on_retry=None, model=None):
        return ProviderResponse(content=json.dumps({"raw_output": "OUT"}))


def test_planner_parses_roles():
    agent = PlannerAgent(provider=None)
    plan = agent._parse_plan(json.dumps(ROLED_PLAN))
    assert [t.role for t in plan.tasks] == ["Researcher", "Analyst", "Critic"]
    print("PASS  planner parses per-task roles")


def test_planner_prompt_requires_roles():
    prompt = PlannerAgent.SYSTEM_PROMPT
    assert '"role"' in prompt and "Researcher" in prompt
    assert "do NOT reuse the same role" in prompt.lower() or "not reuse the same" in prompt.lower()
    print("PASS  planner prompt instructs goal-specific role naming")


def test_roles_reach_graph_payload():
    orch_main.SessionLocal = FakeSession
    ws = FakeWebSocket()
    orch = orch_main.AI_TEAM_ORCHESTRATOR()
    for agent in ("planner", "worker", "reviewer"):
        getattr(orch, agent).provider = RoleProvider()
    asyncio.run(orch.run("role goal", websocket=ws))
    frame = ws.has(lambda f: f["type"] == "planning_completed")
    assert frame, "no planning_completed frame"
    roles = [t["role"] for t in frame["data"]["tasks"]]
    assert roles == ["Researcher", "Analyst", "Critic"], roles
    print("PASS  planning_completed carries role per task for the dashboard")


if __name__ == "__main__":
    test_planner_parses_roles()
    test_planner_prompt_requires_roles()
    test_roles_reach_graph_payload()
    print("\nAll Milestone 24 role tests passed.")
