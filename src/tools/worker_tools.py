"""OpenAI-compatible tool schemas and implementations for the worker agent.

Each entry in TOOL_REGISTRY maps a tool name (as advertised in WORKER_TOOLS)
to a plain synchronous Python callable. The provider's tool loop resolves
model-requested calls through this registry, so unknown or hallucinated tool
names can be rejected safely without executing anything.

Both tools return JSON strings and never raise: failures are surfaced to the
model as structured error payloads so the tool loop can keep going.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

from ddgs import DDGS

WEB_SEARCH_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for factual, up-to-date information on a topic. "
            "Returns live DuckDuckGo result titles, URLs, and snippets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to run.",
                }
            },
            "required": ["query"],
        },
    },
}

READ_FILE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read the text contents of a file inside the project workspace. "
            "Paths are sandboxed to the project root; outside files are rejected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Path to the file, relative to the workspace root.",
                }
            },
            "required": ["filepath"],
        },
    },
}

WRITE_FILE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Write (create or overwrite) a text file inside the project workspace. "
            "Paths are sandboxed to the project root; secrets and outside paths are "
            "rejected. Returns the stored path on success — only claim a file was "
            "saved after this tool reports status ok."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Target path, relative to the workspace root.",
                },
                "content": {
                    "type": "string",
                    "description": "The full text content to write.",
                },
            },
            "required": ["filepath", "content"],
        },
    },
}

WORKER_TOOLS: List[Dict[str, Any]] = [WEB_SEARCH_TOOL, READ_FILE_TOOL, WRITE_FILE_TOOL]


MAX_SEARCH_RESULTS = 5
MAX_FILE_CHARS = 100_000
MAX_WRITE_CHARS = 200_000
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Files the LLM must never touch, even though they live inside the sandbox.
_denied_names = {".env"}
_denied_suffixes = {".key", ".pem", ".p12"}


def _sandbox_target(filepath: str) -> tuple[Path | None, str | None]:
    """Resolve a model-supplied path inside the project, or return an error JSON."""
    try:
        candidate = (PROJECT_ROOT / filepath).resolve() if not Path(filepath).is_absolute() \
            else Path(filepath).resolve()
    except (OSError, ValueError):
        return None, json.dumps({"status": "error", "filepath": filepath,
                                 "error": "Invalid path."})
    if not candidate.is_relative_to(PROJECT_ROOT):
        return None, json.dumps(
            {"status": "error", "filepath": filepath,
             "error": "Access denied: path escapes the project workspace."}
        )
    if candidate.name in _denied_names or candidate.suffix in _denied_suffixes:
        return None, json.dumps(
            {"status": "error", "filepath": filepath,
             "error": "Access denied: this file type is off-limits to the agent."}
        )
    return candidate, None


def web_search(query: str) -> str:
    """Search DuckDuckGo and return live results as a JSON string."""
    try:
        with DDGS() as ddgs:
            raw_results = ddgs.text(query, max_results=MAX_SEARCH_RESULTS) or []
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw_results
        ]
        if not results:
            return json.dumps(
                {"status": "empty", "query": query, "results": [],
                 "note": "The search returned no results. Try a different query."}
            )
        return json.dumps({"status": "ok", "query": query, "results": results})
    except Exception as e:
        return json.dumps(
            {"status": "error", "query": query,
             "error": f"Web search failed: {type(e).__name__}: {e}"}
        )


def _decode_text(data: bytes) -> str:
    """Decode file bytes using BOM sniffing, then UTF-8, with a lossy fallback."""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def read_file(filepath: str) -> str:
    """Read a text file from inside the project sandbox, safely."""
    try:
        candidate, err = _sandbox_target(filepath)
        if err:
            return err
        assert candidate is not None
        if not candidate.is_file():
            return json.dumps(
                {"status": "error", "filepath": filepath,
                 "error": "File not found in the project workspace."}
            )

        contents = _decode_text(candidate.read_bytes())
        truncated = len(contents) > MAX_FILE_CHARS
        if truncated:
            contents = contents[:MAX_FILE_CHARS]

        return json.dumps(
            {
                "status": "ok",
                "filepath": str(candidate.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "truncated": truncated,
                "contents": contents,
            }
        )
    except Exception as e:
        return json.dumps(
            {"status": "error", "filepath": filepath,
             "error": f"File read failed: {type(e).__name__}: {e}"}
        )


def write_file(filepath: str, content: str) -> str:
    """Write a text file inside the project sandbox, safely."""
    try:
        candidate, err = _sandbox_target(filepath)
        if err:
            return err
        assert candidate is not None
        text = str(content)
        if len(text) > MAX_WRITE_CHARS:
            return json.dumps(
                {"status": "error", "filepath": filepath,
                 "error": f"Content too large ({len(text)} chars, max {MAX_WRITE_CHARS})."}
            )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not text mode: Windows would translate \n to \r\n and the
        # review step must read back exactly what the worker wrote.
        candidate.write_bytes(text.encode("utf-8"))
        return json.dumps(
            {
                "status": "ok",
                "filepath": str(candidate.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "bytes_written": len(text.encode("utf-8")),
            }
        )
    except Exception as e:
        return json.dumps(
            {"status": "error", "filepath": filepath,
             "error": f"File write failed: {type(e).__name__}: {e}"}
        )


TOOL_REGISTRY: Dict[str, Callable[..., str]] = {
    "web_search": web_search,
    "read_file": read_file,
    "write_file": write_file,
}
