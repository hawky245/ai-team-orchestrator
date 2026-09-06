"""Worker Agent – executes a single task sequentially."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from src.providers.base_provider import AbstractLLMProvider, ProviderResponse
from src.schemas.models import Task, WorkerResult
from src.utils.json_parser import _extract_and_parse_json


WORKER_SCHEMA = {
    "type": "object",
    "properties": {
        "raw_output": {"type": "string"},
        "artifacts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["raw_output", "artifacts"],
}


class WorkerAgent:
    """Agent that executes a single task and returns its output."""

    SYSTEM_PROMPT = """Your response must start immediately with a left curly brace '{'. Do not write any thoughts, words, reasoning, or introductions before it.

You are a worker agent.

Return ONLY valid JSON - no exceptions.

Rules:
1. Output must begin with {
2. Output must end with }
3. No explanations, reasoning, or additional text
4. No markdown, code fences, or formatting
5. No text before or after the JSON object
6. The entire response must be parseable as JSON
7. If any text precedes the opening {, the generation fails instantly

Required format:
{
    "raw_output": "Your task result here - can contain multiline strings, code, SQL, or Markdown properly escaped as JSON strings",
    "artifacts": []
}

Important: The raw_output field can contain any text content including newlines, code blocks, SQL queries, etc. This content must be properly escaped as a JSON string (e.g., newlines as \\n, quotes as \\\", etc.). Do not include unescaped raw content that would break JSON parsing.
"""

    def __init__(self, provider: AbstractLLMProvider) -> None:
        self.provider = provider

    async def execute(self, task: Task) -> WorkerResult:
        """Execute the given task and return a WorkerResult."""

        prompt = (
            f"Task Description:\n{task.description}\n\n"
            f"Context: {task.context}\n\n"
            f"Dependencies to consider: {task.dependencies}\n\n"
            f"Produce the deliverable for this task."
        )

        response: ProviderResponse = await self.provider.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            schema=WORKER_SCHEMA,
        )

        print("\n=== RAW MODEL RESPONSE ===")
        print(response.content)
        print("==========================\n")

        worker_result = self._parse_result(response.content)
        return worker_result

    def _parse_result(self, raw_text: str) -> WorkerResult:
        """Parse the provider's structured JSON response."""

        data = _extract_and_parse_json(raw_text)

        raw_output = data.get("raw_output", "")
        artifacts = data.get("artifacts", [])

        # Normalize artifacts into Artifact objects (for consistency)
        normalized_artifacts: list = []

        for a in artifacts:
            if isinstance(a, str):
                normalized_artifacts.append(
                    {
                        "artifact_type": "text",
                        "content": a,
                    }
                )

            elif isinstance(a, dict):
                normalized_artifacts.append(
                    {
                        "artifact_type": a.get("artifact_type", "text"),
                        "filename": a.get("filename"),
                        "content": a.get("content", ""),
                    }
                )

            else:
                normalized_artifacts.append(
                    {
                        "artifact_type": "text",
                        "content": str(a),
                    }
                )

        return WorkerResult(
            raw_output=raw_output,
            artifacts=normalized_artifacts,
        )