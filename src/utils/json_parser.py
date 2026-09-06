"""Shared JSON parsing utilities for LLM responses."""

import json
import re
from typing import Any


class JSONParseError(Exception):
    """Raised when LLM response cannot be parsed as valid JSON."""
    pass


def _extract_and_parse_json(response_text: str) -> dict[str, Any]:
    """
    Extract and parse JSON from LLM response text.

    Handles:
    - Markdown code fences (```json ... ```)
    - Extra text before/after JSON
    - Multiline strings and escaped characters
    - Locates first { and last } to isolate JSON block

    Args:
        response_text: Raw response text from LLM

    Returns:
        Parsed JSON as dictionary

    Raises:
        JSONParseError: If response is empty, malformed, or truncated
    """
    if not response_text or not response_text.strip():
        raise JSONParseError("Empty response from LLM")

    text = response_text.strip()

    # Step 1: Try to parse as-is (in case it's already clean JSON)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Step 2: Remove markdown code fences
    # Pattern matches ```json ... ``` or ``` ... ```
    fence_pattern = r'```(?:json)?\s*(.*?)\s*```'
    match = re.search(fence_pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        # Try parsing the content inside the fence
        inner_text = match.group(1).strip()
        try:
            return json.loads(inner_text)
        except json.JSONDecodeError:
            # If that fails, continue to next step
            pass

    # Step 3: Find the first '{' and last '}' to isolate JSON object
    first_brace = text.find('{')
    last_brace = text.rfind('}')

    if first_brace == -1 or last_brace == -1 or first_brace > last_brace:
        raise JSONParseError("No JSON object found in response")

    json_str = text[first_brace:last_brace + 1]

    # Step 4: Try to parse the isolated JSON
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        # Provide more context about the error
        raise JSONParseError(
            f"Failed to parse JSON after extraction. "
            f"Error: {str(e)}. Extracted text: {json_str[:200]}..."
        ) from e