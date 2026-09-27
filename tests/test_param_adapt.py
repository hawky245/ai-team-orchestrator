"""Milestone 16b: per-model max_tokens adaptation + friendly auth errors.

Run:  PYTHONPATH=. venv/Scripts/python.exe tests/test_param_adapt.py
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("LLM_API_KEY", "sk-secret-xyz")

import httpx2 as httpx
import openai

from src.providers.base_provider import (
    NvidiaNimProvider,
    ProviderConfig,
    ProviderError,
)

CFG = ProviderConfig(model_id="cap/model", temperature=0.0, max_tokens=8192)


def _status_err(cls, status: int, message: str):
    req = httpx.Request("POST", "https://example.invalid")
    return cls(message, response=httpx.Response(status, request=req), body=None)


class Client:
    """Fake completions client recording every requested max_tokens."""

    def __init__(self, reject):
        self.seen: list[int] = []
        self.reject = reject  # (kwargs) -> Exception | None
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.seen.append(kwargs.get("max_tokens"))
                err = outer.reject(kwargs)
                if err:
                    raise err
                msg = type("M", (), {"content": '{"ok": true}', "tool_calls": None})()
                choice = type("C", (), {"message": msg, "finish_reason": "stop"})()
                return type("R", (), {"choices": [choice], "usage": None})()

        self.chat = type("Ch", (), {"completions": Completions()})()


def provider_with(reject):
    p = NvidiaNimProvider(CFG)
    p.client = Client(reject)
    p.api_keys = ["k"]
    p.RETRY_BASE_DELAY = 0.001
    return p


def test_max_tokens_halving():
    # Model caps output at 4096: first 8192 call 400s, halved retry succeeds.
    p = provider_with(
        lambda kw: _status_err(openai.BadRequestError, 400,
                                "'max_tokens' must be <= 4096")
        if kw["max_tokens"] > 4096 else None
    )
    out = asyncio.run(p.generate("s", "u", schema={}))
    assert out.content == '{"ok": true}'
    assert p.client.seen == [8192, 4096], p.client.seen
    print("PASS  max_tokens 400 halves and retries in place (8192 -> 4096)")


def test_halving_floor_and_unrelated_400():
    # Every size rejected: shrinks to the 512 floor, then surfaces the error.
    p = provider_with(
        lambda kw: _status_err(openai.BadRequestError, 400, "max_tokens too large")
    )
    try:
        asyncio.run(p.generate("s", "u", schema={}))
        raise AssertionError("expected ProviderError")
    except ProviderError as e:
        assert "max_tokens too large" in str(e)
    assert p.client.seen == [8192, 4096, 2048, 1024, 512], p.client.seen

    # A 400 about something else must NOT trigger the shrink loop.
    p2 = provider_with(
        lambda kw: _status_err(openai.BadRequestError, 400, "unknown parameter 'temp'")
    )
    try:
        asyncio.run(p2.generate("s", "u", schema={}))
        raise AssertionError("expected ProviderError")
    except ProviderError:
        pass
    assert p2.client.seen == [8192], p2.client.seen
    print("PASS  shrink stops at the 512 floor; unrelated 400s pass straight through")


def test_auth_error_is_actionable():
    for exc in (
        _status_err(openai.AuthenticationError, 401, "Unauthorized"),
        _status_err(openai.PermissionDeniedError, 403, "Forbidden"),
    ):
        p = provider_with(lambda kw, exc=exc: exc)
        try:
            asyncio.run(p.generate("s", "u", schema={}))
            raise AssertionError("expected ProviderError")
        except ProviderError as e:
            assert "rejected your API key" in str(e), str(e)
            assert "Model Bay" in str(e)          # explains the public-listing trap
            assert "sk-secret-xyz" not in str(e)  # never echoes the key
    print("PASS  401/403 surface as an actionable key message without leaking it")


def test_tool_calling_degradation():
    calls: list[bool] = []  # per call: were tools sent?

    class ToolRejectClient:
        def __init__(self):
            class Completions:
                def create(self, **kwargs):
                    calls.append("tools" in kwargs)
                    if "tools" in kwargs:
                        raise _status_err(
                            openai.BadRequestError, 400,
                            "'tool calling' is not supported for this model",
                        )
                    msg = type("M", (), {"content": '{"raw_output": "done", "artifacts": []}',
                                         "tool_calls": None})()
                    choice = type("C", (), {"message": msg, "finish_reason": "stop"})()
                    return type("R", (), {"choices": [choice], "usage": None})()

            self.chat = type("Ch", (), {"completions": Completions()})()

    p = NvidiaNimProvider(CFG)
    p.client = ToolRejectClient()
    p.api_keys = ["k"]
    p.RETRY_BASE_DELAY = 0.001
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    out = asyncio.run(p.generate_with_tools("s", "u", tools, {}))
    assert out.content == '{"raw_output": "done", "artifacts": []}'
    assert calls == [True, False], calls   # tried with tools, retried without
    print("PASS  tool-calling 400 degrades to a plain completion in-run")


if __name__ == "__main__":
    test_max_tokens_halving()
    test_halving_floor_and_unrelated_400()
    test_auth_error_is_actionable()
    test_tool_calling_degradation()
    print("\nAll max_tokens/auth adaptation tests passed.")
