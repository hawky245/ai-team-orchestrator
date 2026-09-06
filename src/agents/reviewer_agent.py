"""Reviewer Agent – validates task output and decides acceptance."""

from __future__ import annotations

import json
from typing import Any, List

from pydantic import BaseModel, Field

from src.providers.base_provider import AbstractLLMProvider, ProviderResponse
from src.schemas.models import ReviewResult, Task
from src.utils.json_parser import _extract_and_parse_json


REVIEWER_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "feedback": {"type": "string"},
    },
    "required": ["approved", "feedback"],
}


class ReviewerAgent:
    """Agent that evaluates a Worker output and decides whether it is acceptable."""

    SYSTEM_PROMPT = """You are a **Reviewer Agent** in an AI team.
Your job is to evaluate the Worker's output against the original task description and decide if it is correct, complete, and properly formatted.

OUTPUT FORMAT:
You MUST respond with a **single JSON object** that matches this exact schema:
{
    "approved": true or false,
    "feedback": "Detailed explanation of what is good or needs improvement"
}

RULES:
- Respond with ONLY the JSON object – no extra text, no markdown, no code fences.
- approved should be true only if the output fully and correctly addresses the task.
- feedback should be constructive, pointing out what works and what doesn't.
"""

    def __init__(self, provider: AbstractLLMProvider) -> None:
        self.provider = provider

    async def evaluate(self, task: Task, worker_result: Any) -> ReviewResult:
        """Evaluate the worker output and return a structured ReviewResult."""
        prompt = f"""Task Description:
{task.description}

Worker Output:
{worker_result.raw_output}

Context: {task.context}

Dependencies considered: {task.dependencies}

Evaluate the worker output. Is it correct, complete, and properly formatted? Provide feedback, any issues found, suggestions for improvement, whether a retry is allowed, and a score from 0.0 to 1.0."""

        response: ProviderResponse = await self.provider.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            schema=REVIEWER_SCHEMA,
        )

        review = self._parse_result(response.content)
        return review

    def _parse_result(self, raw_text: str) -> ReviewResult:
        """Parse the provider's structured JSON review."""
        data = _extract_and_parse_json(raw_text)

        is_valid = data.get("approved", False)
        feedback = data.get("feedback", "")
        issues = data.get("issues", [])
        if not isinstance(issues, list):
            issues = [str(issues)]
        suggestions = data.get("suggestions", [])
        if not isinstance(suggestions, list):
            suggestions = [str(suggestions)]
        retry_allowed = data.get("retry_allowed", True)
        score = float(data.get("score", 0.0))

        if not 0.0 <= score <= 1.0:
            score = 0.0

        return ReviewResult(
            is_valid=is_valid,
            feedback=feedback,
            issues=issues,
            suggestions=suggestions,
            retry_allowed=retry_allowed,
            score=score,
        )