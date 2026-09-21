"""Milestone 7 verification: retry decorator, transient classification, fallback routing.

Run from the repo root:  PYTHONPATH=. venv/Scripts/python.exe tests/test_retry.py
"""
import asyncio
import os

import httpx2 as httpx
import openai

from src.utils.retry import retry_async, compute_delay
from src.providers.base_provider import (
    NvidiaNimProvider,
    ProviderConfig,
    ProviderError,
    ModelExhaustedError,
    _is_transient_error,
)

os.environ.setdefault("LLM_API_KEY", "dummy-key-for-constructor")

req = httpx.Request("POST", "http://fake/v1/chat/completions")


def status_error(cls, code):
    resp = httpx.Response(code, request=req)
    return cls("boom", response=resp, body=None)


def conn_timeout(cls):
    return cls(request=req)


# ---- 1. retry_async decorator ----

def test_retry_async():
    calls = {"n": 0}
    events = []

    async def on_retry(attempt, delay, exc):
        events.append((attempt, delay, str(exc)))

    flaky_counter = {"n": 0}

    @retry_async(max_retries=3, base_delay=0.001, is_retryable=lambda e: isinstance(e, ValueError), on_retry=on_retry)
    async def flaky():
        calls["n"] += 1
        flaky_counter["n"] += 1
        if flaky_counter["n"] < 3:
            raise ValueError("transient")
        return "ok"

    result = asyncio.run(flaky())
    assert result == "ok" and calls["n"] == 3, (result, calls)
    assert [e[0] for e in events] == [1, 2], events
    print("[retry_async] PASS: succeeded on try 3, on_retry fired for failed tries 1,2")

    # non-retryable raises immediately
    @retry_async(max_retries=5, is_retryable=lambda e: False)
    async def fatal():
        calls["n"] += 1
        raise KeyError("permanent")

    try:
        asyncio.run(fatal())
        raise AssertionError("should have raised")
    except KeyError:
        pass
    print("[retry_async] PASS: non-retryable error raised on first try, no sleep")

    # exhaustion re-raises last error
    @retry_async(max_retries=2, base_delay=0.001)
    async def always():
        raise ValueError("still failing")

    try:
        asyncio.run(always())
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "still failing" in str(e)
    print("[retry_async] PASS: exhaustion re-raises the original exception")

    # sync callables supported
    @retry_async(max_retries=3, base_delay=0.001)
    def sync_fn():
        return 42

    assert asyncio.run(sync_fn()) == 42
    print("[retry_async] PASS: sync callable supported")


def test_compute_delay():
    for attempt in range(1, 8):
        d = compute_delay(attempt, base_delay=1.0, backoff_factor=2.0, max_delay=30.0)
        ceiling = min(1.0 * 2 ** (attempt - 1), 30.0)
        assert 0 <= d <= ceiling, (attempt, d, ceiling)
    no_jitter = compute_delay(3, base_delay=1.0, backoff_factor=2.0, jitter=False)
    assert no_jitter == 4.0
    print("[compute_delay] PASS: exponential growth, capped at max_delay, jitter within bounds")


# ---- 2. transient classification ----

def test_is_transient():
    assert _is_transient_error(status_error(openai.InternalServerError, 503)), "503 capacity spike"
    assert _is_transient_error(status_error(openai.RateLimitError, 429)), "429 rate limit"
    assert _is_transient_error(conn_timeout(openai.APITimeoutError)), "timeout"
    assert _is_transient_error(conn_timeout(openai.APIConnectionError)), "connection"
    assert _is_transient_error(status_error(openai.APIStatusError, 503))
    assert not _is_transient_error(status_error(openai.BadRequestError, 400)), "400 must not retry"
    assert not _is_transient_error(status_error(openai.AuthenticationError, 401))
    assert not _is_transient_error(RuntimeError("unrelated"))
    print("[is_transient] PASS: 503/429/timeout retryable; 400/401/unrelated not")


# ---- 3. provider backoff + fallback routing ----

class ScriptedClient:
    """chat.completions.create stub driven by per-model action scripts."""

    def __init__(self, scripts):
        self.scripts = {m: list(a) for m, a in scripts.items()}
        self.models_called = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        model = kwargs["model"]
        self.models_called.append(model)
        action = self.scripts[model].pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def make_provider():
    p = NvidiaNimProvider(ProviderConfig(model_id="primary-m", temperature=0.1, max_tokens=256))
    p.RETRY_BASE_DELAY = 0.001  # keep tests fast
    return p


def ok_response():
    msg = type("M", (), {"content": "hello", "tool_calls": None})()
    return type("R", (), {"choices": [type("C", (), {"message": msg, "finish_reason": "stop"})()], "usage": None})()


def test_provider_transient_retry():
    p = make_provider()
    err = status_error(openai.APIStatusError, 503)
    p.client = ScriptedClient({"primary-m": [err, err, ok_response()]})
    seen = []

    async def on_retry(attempt, delay, exc):
        seen.append(attempt)

    resp = asyncio.run(p._create_with_backoff({"messages": []}, on_retry))
    assert resp.choices[0].message.content == "hello"
    assert seen == [1, 2], seen
    print("[provider retry] PASS: two 503 spikes absorbed, on_retry fired twice, run not failed")


def test_provider_fallback_routing():
    os.environ["LLM_FALLBACK_MODEL_ID"] = "fallback-m"
    try:
        p = make_provider()
        exhaust = [status_error(openai.RateLimitError, 429)] * 3
        p.client = ScriptedClient({
            "primary-m": exhaust,
            "fallback-m": [ok_response()],
        })
        resp = asyncio.run(p._create_with_backoff({"messages": []}, None))
        assert resp.choices[0].message.content == "hello"
        assert p.client.models_called == ["primary-m"] * 3 + ["fallback-m"], p.client.models_called
        print("[fallback] PASS: primary 429-exhausted -> routed to fallback model, success")
    finally:
        del os.environ["LLM_FALLBACK_MODEL_ID"]


def test_provider_exhausted_no_fallback():
    p = make_provider()
    p.client = ScriptedClient({"primary-m": [status_error(openai.APIStatusError, 503)] * 3})
    try:
        asyncio.run(p._create_with_backoff({"messages": []}, None))
        raise AssertionError("expected ModelExhaustedError")
    except ModelExhaustedError as e:
        assert e.primary_model == "primary-m"
        assert e.fallback_model is None
        assert e.attempts == 3
        assert isinstance(e.last_error, openai.APIStatusError)
        assert isinstance(e, ProviderError), "must stay catchable as ProviderError downstream"
        print("[fallback] PASS: exhausted primary raises structured ModelExhaustedError (ProviderError-compatible)")


def test_provider_non_transient_fast_fail():
    p = make_provider()
    p.client = ScriptedClient({"primary-m": [status_error(openai.BadRequestError, 400)]})
    try:
        asyncio.run(p._create_with_backoff({"messages": []}, None))
        raise AssertionError("expected BadRequestError")
    except openai.BadRequestError:
        assert p.client.models_called == ["primary-m"], "400 must not retry or fallback"
        print("[fallback] PASS: non-transient 400 raises on first call, no retry, no fallback")


if __name__ == "__main__":
    test_retry_async()
    test_compute_delay()
    test_is_transient()
    test_provider_transient_retry()
    test_provider_fallback_routing()
    test_provider_exhausted_no_fallback()
    test_provider_non_transient_fast_fail()
    print("\nALL MILESTONE-7 RETRY CASES PASS")
