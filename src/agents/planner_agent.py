"""Planner Agent – decomposes a user goal into 3–7 sequential tasks."""

from __future__ import annotations

import json
from typing import Any, List

from pydantic import BaseModel, Field

from src.providers.base_provider import AbstractLLMProvider, ProviderResponse
from src.schemas.models import Goal, PlannerOutput, Task, TaskPlan
from src.utils.json_parser import _extract_and_parse_json


PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "description": {"type": "string"},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "is_parallel": {"type": "boolean"},
                    "requires_user_input": {"type": "boolean"},
                    "context": {"type": "object"},
                },
                "required": ["task_id", "description"],
            },
        },
        "execution_order": {"type": "string"},
    },
    "required": ["summary", "tasks", "execution_order"],
}


class PlannerAgent:
    """Agent that breaks down a high-level goal into an ordered list of tasks."""

    SYSTEM_PROMPT = """You are a **Planner Agent** in an AI team.
Your job is to take a user goal and break it down into 3–7 clear, actionable tasks.

OUTPUT FORMAT:
You MUST respond with ONLY a single JSON object that matches this exact schema (no exceptions):
{
  "summary": "Brief summary of the overall plan",
  "tasks": [
    {"task_id": "t1", "description": "Specific, actionable task description", "depends_on": [], "is_parallel": false, "context": {}},
    {"task_id": "t2", "description": "Independent task, needs no other output", "depends_on": [], "is_parallel": true, "context": {}},
    {"task_id": "t3", "description": "Task that combines t1 and t2", "depends_on": ["t1", "t2"], "is_parallel": false, "context": {}}
  ],
  "execution_order": "dag"
}

RULES:
- Generate EXACTLY 3 to 7 tasks (not less, not more).
- Tasks should be concrete and specific (not vague).
- "depends_on" lists the task IDs whose output this task needs before it can start.
- The orchestrator runs tasks CONCURRENTLY whenever their depends_on allow it:
  give a task "depends_on": [] (and optionally "is_parallel": true) ONLY when it
  genuinely does not need any other task's output.
- Add depends_on entries for every task that builds on, polishes, or reviews
  another task's output.
- If a task is ambiguous, risky, or needs explicit user approval before the
  worker may start, set "requires_user_input": true on it. The orchestrator
  will pause that task and ask the user for direction first.
- Set "execution_order" to "dag" when the plan contains a dependency structure
  (the usual case). Use "sequential" only when EVERY task requires the output
  of the task directly before it.
- Each task_id must be unique and follow pattern t1, t2, t3...
- DO NOT include any explanations, reasoning, markdown, code fences, or text before/after the JSON.
- Your entire response must be valid JSON that can be parsed directly.
"""

    def __init__(self, provider: AbstractLLMProvider) -> None:
        self.provider = provider

    async def execute(self, goal: str) -> PlannerOutput:
        """Run the planner on the given goal and return a structured plan."""
        prompt = f"User Goal:\n{goal}\n\nReturn the task plan as JSON."
        response: ProviderResponse = await self.provider.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            schema=PLANNER_SCHEMA,
        )

        plan = self._parse_plan(response.content)
        planner_out = PlannerOutput(
            raw_text=response.content,
            tasks=plan.tasks,
            plan_summary=plan.summary,
            execution_order=plan.execution_order,
        )
        return planner_out

    def _parse_plan(self, raw_text: str) -> TaskPlan:
        """Parse and validate the provider's structured JSON plan."""
        data = _extract_and_parse_json(raw_text)

        # Validate required fields
        if "tasks" not in data or not isinstance(data["tasks"], list):
            raise ValueError("Planner output missing 'tasks' list")

        tasks: List[Task] = []
        for idx, t in enumerate(data["tasks"], 1):
            if not isinstance(t, dict):
                raise ValueError(f"Task {idx} is not an object")

            task_id = t.get("task_id", f"t{idx}")
            description = t.get("description", "").strip()
            if not description:
                raise ValueError(f"Task {idx} has empty description")

            # Accept both spellings of the dependency list: the new
            # `depends_on` (Milestone 8 spec) and legacy `dependencies`.
            raw_deps = []
            seen_deps = set()
            for key in ("depends_on", "dependencies"):
                value = t.get(key, [])
                if isinstance(value, list):
                    for d in value:
                        if isinstance(d, str) and d not in seen_deps:
                            seen_deps.add(d)
                            raw_deps.append(d)

            context = t.get("context", {})
            if not isinstance(context, dict):
                context = {}

            is_parallel = t.get("is_parallel", False)
            if not isinstance(is_parallel, bool):
                is_parallel = bool(is_parallel)

            requires_user_input = t.get("requires_user_input", False)
            if not isinstance(requires_user_input, bool):
                requires_user_input = bool(requires_user_input)

            tasks.append(
                Task(
                    task_id=task_id,
                    description=description,
                    dependencies=raw_deps,
                    is_parallel=is_parallel,
                    requires_user_input=requires_user_input,
                    context=context,
                )
            )

        if len(tasks) < 3:
            raise ValueError(f"Planner produced only {len(tasks)} tasks (minimum 3)")
        if len(tasks) > 7:
            raise ValueError(f"Planner produced {len(tasks)} tasks (maximum 7)")

        summary = data.get("summary", "Plan generated by Planner Agent")

        return TaskPlan(
            summary=summary,
            tasks=tasks,
            execution_order=data.get("execution_order", "sequential"),
        )