"""LLM provider: an OpenAI-compatible client for NVIDIA NIM, Groq, OpenRouter, etc."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import openai
from pydantic import BaseModel, Field

from src.utils.retry import retry_async


class ProviderConfig(BaseModel):
    """Configuration for a single provider."""
    model_id: str = Field(..., description="Model identifier for the provider")
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=1024, ge=1)


class ProviderResponse(BaseModel):
    """Normalized response from the provider.

    Token counts feed the dashboard's per-task and per-run meters — the
    audit cut them once for having no consumers; M22 earned them back.
    In the tool loop they accumulate across ALL rounds, so the number
    reflects what the task actually cost.
    """
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ProviderError(Exception):
    """Custom exception for provider-level errors."""
    pass


class ModelExhaustedError(ProviderError):
    """All retries (and the fallback model, when configured) are spent.

    Carries structured fallback state so downstream agents can mark the task
    failed cleanly instead of handling a raw provider crash.
    """

    def __init__(
        self,
        message: str,
        *,
        primary_model: str,
        fallback_model: str | None,
        attempts: int,
        last_error: Exception,
    ) -> None:
        super().__init__(message)
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.attempts = attempts
        self.last_error = last_error


# HTTP statuses worth retrying: capacity spikes (5xx), rate limits (429),
# and timeout-adjacent codes. Everything else (400, 401, 404, ...) is a
# permanent failure that must surface immediately.
TRANSIENT_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError),
    ):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in TRANSIENT_STATUS_CODES


def _is_max_tokens_error(exc: Exception) -> bool:
    """400 rejecting the requested max_tokens size (model caps it lower)."""
    return isinstance(exc, openai.BadRequestError) and "max_tokens" in str(exc).lower()


def _is_tool_unsupported_error(exc: Exception) -> bool:
    """400 saying this model cannot do function/tool calling at all."""
    if not isinstance(exc, openai.BadRequestError):
        return False
    msg = str(exc).lower()
    return "tool" in msg and "support" in msg


def _friendly_auth_error(exc: Exception) -> "ProviderError | None":
    """Turn raw 401/403s into an actionable message, never echoing the key.

    Worth calling out explicitly because several providers serve their model
    LIST publicly: an invalid key still fills the dashboard's Model Bay, so
    the first real completion is where the key actually fails.
    """
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ProviderError(
            "The provider rejected your API key (401/403). This provider lists "
            "its models publicly, so the Model Bay can look fine even with a "
            "bad key — the first real request proves it. Re-add the key in the "
            "Key Ring or remove it to use the server key."
        )
    return None


# Key-prefix and host fingerprints used to label which provider a model list
# came from. Returns a display name only — never any key material.
_KEY_PREFIXES = (
    ("nvapi-", "NVIDIA"),
    ("sk-or-", "OpenRouter"),
    ("gsk_", "Groq"),
    ("sk-ant-", "Anthropic"),
    ("AIza", "Google Gemini"),
    ("sk-", "OpenAI"),
)
_HOST_HINTS = (
    ("nvidia.com", "NVIDIA"),
    ("openrouter.ai", "OpenRouter"),
    ("groq.com", "Groq"),
    ("anthropic.com", "Anthropic"),
    ("openai.com", "OpenAI"),
    ("googleapis.com", "Google Gemini"),
)


def detect_provider_label(api_key: str | None) -> str:
    """Human-readable provider name for the dashboard badge."""
    key = (api_key or "").strip()
    if key:
        for prefix, label in _KEY_PREFIXES:
            if key.startswith(prefix):
                return label
        return "Custom endpoint"
    base = (os.getenv("LLM_BASE_URL") or "").lower()
    if not base:
        return "OpenAI"
    if "localhost" in base or "127.0.0.1" in base:
        return "Local"
    for host, label in _HOST_HINTS:
        if host in base:
            return label
    return "Custom endpoint"


# Public OpenAI-compatible endpoints for keys that are unambiguously
# identifiable by prefix. Ordered most-specific first; a bare `sk-` is NOT
# routed here because it is ambiguous (legacy OpenAI, DeepSeek, Moonshot,
# API gateways all use it) and keeps the configured base URL instead.
_PROVIDER_BASE_URLS = (
    ("nvapi-", "https://integrate.api.nvidia.com/v1"),
    ("gsk_", "https://api.groq.com/openai/v1"),
    ("sk-or-", "https://openrouter.ai/api/v1"),
    ("sk-ant-", "https://api.anthropic.com/v1"),
    ("AIza", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("sk-proj-", "https://api.openai.com/v1"),
    ("sk-svcacct-", "https://api.openai.com/v1"),
)


def base_url_for_key(api_key: str | None, default: str | None = None) -> str | None:
    """The endpoint this key should talk to.

    A recognisable key prefix wins over `default` — a Groq key is useless at
    the NVIDIA URL and vice versa. Local endpoints (dev proxies, Ollama,
    LM Studio) are always kept as configured.
    """
    base = (default or "").lower()
    if "localhost" in base or "127.0.0.1" in base:
        return default
    key = (api_key or "").strip()
    for prefix, url in _PROVIDER_BASE_URLS:
        if key.startswith(prefix):
            return url
    return default


class NvidiaNimProvider:
    """Provider that talks to any OpenAI-compatible LLM via the OpenAI Python client.

    Supports rotating through multiple API keys (comma‑separated in ``LLM_API_KEY``)
    for load‑balancing or fallback when a key hits rate limits. The first key is used
    by default; subsequent calls rotate round‑robin.

    An ``api_key`` passed to the constructor (e.g. supplied by a user through the
    dashboard) replaces the environment keys entirely; commas still split it into
    a rotation set.
    """

    def __init__(self, config: ProviderConfig, api_key: str | None = None) -> None:
        self.config = config

        # Provider-agnostic env vars: any .env setting these will work
        # across OpenAI, Gemini, OpenRouter, or local OpenAI-compatible hosts.
        # A caller-provided key (dynamic user key) wins over the environment.
        raw_key = api_key.strip() if api_key and api_key.strip() else os.getenv("LLM_API_KEY", "")
        # Allow multiple keys separated by commas for multi‑provider support.
        self.api_keys = [k.strip() for k in raw_key.split(",") if k.strip()]
        if not self.api_keys:
            self.api_keys = [""]  # empty key fallback – client will raise if needed
        self._key_index = 0

        base_url = os.getenv("LLM_BASE_URL")
        if api_key and api_key.strip():
            # A user-supplied key knows its own provider: route to that
            # endpoint instead of the env one (e.g. a Groq key at NVIDIA's
            # URL would list/serve the wrong catalogue entirely).
            base_url = base_url_for_key(self.api_keys[0], base_url)

        # Initialise client with the first key; we'll rotate per request.
        client_kwargs: dict = {"api_key": self.api_keys[0]}
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = openai.OpenAI(**client_kwargs)
        self._base_url = base_url

        # Milestone 15: model-id -> api-key map for multi-provider runs.
        # Tasks whose model was fetched with a specific key keep using it.
        self.model_keys: dict[str, str] = {}
        self._key_clients: dict[str, Any] = {}

    def _client_for_key(self, key: str):
        """Cached client for a specific key, pointed at that key's provider."""
        client = self._key_clients.get(key)
        if client is None:
            kwargs: dict = {"api_key": key}
            base = base_url_for_key(key, self._base_url)
            if base:
                kwargs["base_url"] = base
            client = openai.OpenAI(**kwargs)
            self._key_clients[key] = client
        return client

    def _client_for_model(self, model: str | None):
        """Cached client for the key mapped to this model, else the default."""
        key = self.model_keys.get(model or "")
        if not key:
            return self.client
        return self._client_for_key(key)

    def _next_api_key(self) -> str:
        """Return the next API key in a round‑robin fashion."""
        if len(self.api_keys) <= 1:
            return self.api_keys[0]
        self._key_index = (self._key_index + 1) % len(self.api_keys)
        return self.api_keys[self._key_index]

    async def list_models(self) -> list[str]:
        """Return sorted model IDs visible to this provider's credentials.

        Used by the /api/models proxy so the dashboard can offer a model
        picker without hardcoding model names. Auth failures surface as a
        clean ProviderError — the message never echoes the key itself.
        """
        try:
            response = await asyncio.to_thread(self.client.models.list)
        except openai.AuthenticationError as e:
            raise ProviderError(
                "The provider rejected this API key (401). "
                "Check the key and try again."
            ) from e
        except openai.PermissionDeniedError as e:
            raise ProviderError(
                "This API key is not allowed to list models (403)."
            ) from e
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Model listing failed: {type(e).__name__}: {e}")

        ids = {
            m.id
            for m in (getattr(response, "data", None) or [])
            if getattr(m, "id", None)
        }
        return sorted(ids)

    TRANSIENT_MAX_RETRIES = 3
    FALLBACK_MAX_RETRIES = 2
    RETRY_BASE_DELAY = 1.0

    MAX_TOKENS_FLOOR = 512

    async def _create_completion(self, model: str, request_kwargs: dict):
        """Perform the HTTP call in a worker thread.

        The bundled OpenAI client is synchronous; calling it directly would
        block the event loop for the whole round-trip, silently serializing
        "concurrent" workers and delaying WebSocket frames. to_thread keeps
        the loop free so waves truly overlap.

        When the model has an entry in `model_keys` (multi-key runs), the
        cached client for that key serves the request; otherwise the default
        rotated client is used.

        Small-cap models reject the anti-truncation max_tokens floor with a
        400 naming the parameter; halve and retry in place until the floor,
        then surface the error.
        """
        while True:
            try:
                return await asyncio.to_thread(
                    self._client_for_model(model).chat.completions.create,
                    **{**request_kwargs, "model": model},
                )
            except openai.BadRequestError as e:
                current = request_kwargs.get("max_tokens") or 0
                if not _is_max_tokens_error(e) or current <= self.MAX_TOKENS_FLOOR:
                    raise
                request_kwargs["max_tokens"] = max(
                    self.MAX_TOKENS_FLOOR, current // 2
                )
                print(
                    f"[ADAPT] '{model}' rejected max_tokens={current}; "
                    f"retrying at {request_kwargs['max_tokens']}"
                )

    async def _create_with_retries(
        self,
        model: str,
        request_kwargs: dict,
        on_retry,
        max_retries: int,
    ):
        """One chat-completions call, retried on transient errors only."""
        attempt = retry_async(
            lambda: self._create_completion(model, request_kwargs),
            max_retries=max_retries,
            base_delay=self.RETRY_BASE_DELAY,
            is_retryable=_is_transient_error,
            on_retry=on_retry,
        )
        return await attempt()

    async def _create_with_backoff(
        self,
        request_kwargs: dict,
        on_retry=None,
        model: str | None = None,
    ):
        """Create a completion with resilience layered on top of the raw call.

        `model` (a task-level model_override) takes precedence over the
        configured primary model; the env fallback still applies to it.

        1. Primary model: up to TRANSIENT_MAX_RETRIES tries with exponential
           backoff + jitter on 503/429/timeout-class failures.
        2. If the primary is exhausted and LLM_FALLBACK_MODEL_ID is set, route
           to the fallback model with a smaller retry budget.
        3. If everything is spent, raise ModelExhaustedError carrying the
           structured fallback state instead of an opaque crash.
        Non-transient errors (400/401/404...) propagate on the first try.
        """
        primary = model or self.config.model_id
        attempts = self.TRANSIENT_MAX_RETRIES
        try:
            return await self._create_with_retries(primary, request_kwargs, on_retry, attempts)
        except Exception as primary_exc:
            if not _is_transient_error(primary_exc):
                raise

            fallback = os.getenv("LLM_FALLBACK_MODEL_ID", "").strip()
            if fallback and fallback != primary:
                try:
                    return await self._create_with_retries(
                        fallback, request_kwargs, on_retry, self.FALLBACK_MAX_RETRIES
                    )
                except Exception as fallback_exc:
                    if not _is_transient_error(fallback_exc):
                        raise
                    raise ModelExhaustedError(
                        f"All retries exhausted for model '{primary}' and fallback '{fallback}': "
                        f"{type(fallback_exc).__name__}: {fallback_exc}",
                        primary_model=primary,
                        fallback_model=fallback,
                        attempts=attempts + self.FALLBACK_MAX_RETRIES,
                        last_error=fallback_exc,
                    ) from fallback_exc

            raise ModelExhaustedError(
                f"All retries exhausted for model '{primary}' (no fallback configured): "
                f"{type(primary_exc).__name__}: {primary_exc}",
                primary_model=primary,
                fallback_model=None,
                attempts=attempts,
                last_error=primary_exc,
            ) from primary_exc

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict = None,
        **kwargs: Any,
    ) -> ProviderResponse:

        # Rotate API key before each request to spread load / avoid rate limits.
        if len(self.api_keys) > 1:
            # Rotation can mix providers (comma-separated keys): send each key
            # to its own endpoint via the cached per-key client.
            self.client = self._client_for_key(self._next_api_key())

        temp = kwargs.get("temperature", self.config.temperature)
        on_retry = kwargs.get("on_retry")
        # Task-level model routing: an explicit `model` beats the global config.
        model = kwargs.get("model") or self.config.model_id
        # Ensure max_tokens is at least 8192 to prevent truncation
        configured_max_tok = kwargs.get("max_tokens", self.config.max_tokens)
        max_tok = max(configured_max_tok, 8192)

        # Enable JSON mode when a schema is provided (works with OpenAI‑compatible endpoints)
        # Some endpoints may not support response_format, so we wrap it in a try/except
        response_format = {"type": "json_object"} if schema else None

        request_kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temp,
            "max_tokens": max_tok,
        }

        # Add response_format if supported (some endpoints may reject it with 400)
        if response_format:
            request_kwargs["response_format"] = response_format

        try:
            response = await self._create_with_backoff(request_kwargs, on_retry, model=model)

            # Safely extract the first choice, handling None/empty responses
            if not response.choices:
                raise ProviderError("LLM response had no choices")

            choice = response.choices[0]
            textual = choice.message.content or ""

            # If content is empty after extraction, raise a descriptive error
            if not textual.strip():
                raise ProviderError("LLM response content was empty")

            usage = getattr(response, "usage", None)
            return ProviderResponse(
                content=textual,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0 if usage else 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0 if usage else 0,
            )

        except ProviderError:
            # Re‑raise ProviderError as‑is (no prefix wrapping)
            raise
        except Exception as e:
            friendly = _friendly_auth_error(e)
            if friendly is not None:
                raise friendly from e
            raise ProviderError(
                f"LLM provider request failed: {str(e)}"
            )

    MAX_TOOL_ROUNDS = 5

    async def generate_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list,
        tool_registry: dict,
        on_tool_call=None,
        on_retry=None,
        model: str | None = None,
    ) -> ProviderResponse:
        """Run an OpenAI-compatible tool-calling loop.

        Sends `tools` with the request; whenever the model returns
        ``message.tool_calls``, each call is resolved against `tool_registry`
        (name -> callable), executed locally, and the result is appended as a
        ``role: tool`` message before the next LLM call. The loop ends when the
        model answers with plain content. Unknown/hallucinated tool names and
        malformed arguments are returned to the model as tool errors instead
        of crashing. `on_tool_call` (async) fires just before each real
        execution so callers can broadcast the action.
        """
        import json as _json

        effective_model = model or self.config.model_id
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        # Flipped off when the model 400s on function calling; the per-round
        # kwargs are rebuilt, so a one-shot pop would be undone.
        tools_enabled = bool(tools)
        # Token meter accumulates across every round of the loop, so the
        # per-task figure reflects the true cost of the tool conversation.
        tok_prompt = 0
        tok_comp = 0

        for round_no in range(self.MAX_TOOL_ROUNDS + 1):
            request_kwargs = {
                "model": effective_model,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": max(self.config.max_tokens, 8192),
            }
            if tools_enabled and round_no < self.MAX_TOOL_ROUNDS:
                request_kwargs["tools"] = tools
                request_kwargs["tool_choice"] = "auto"

            try:
                response = await self._create_with_backoff(
                    request_kwargs, on_retry, model=effective_model
                )
            except ProviderError:
                # Keep ModelExhaustedError's structured fallback state intact.
                raise
            except Exception as e:
                if _is_tool_unsupported_error(e) and tools_enabled:
                    # Model has no function calling: drop the tool request and
                    # answer this round as a plain completion instead of
                    # burning all worker attempts on a permanent 400.
                    print(
                        f"[ADAPT] '{effective_model}' does not support tool "
                        "calling; continuing without tools"
                    )
                    tools_enabled = False
                    continue
                friendly = _friendly_auth_error(e)
                if friendly is not None:
                    raise friendly from e
                raise ProviderError(f"LLM provider request failed: {str(e)}")

            if not response.choices:
                raise ProviderError("LLM response had no choices")

            choice = response.choices[0]
            usage = getattr(response, "usage", None)
            if usage:
                tok_prompt += getattr(usage, "prompt_tokens", 0) or 0
                tok_comp += getattr(usage, "completion_tokens", 0) or 0
            message = choice.message

            if not getattr(message, "tool_calls", None):
                textual = message.content or ""
                if not textual.strip():
                    raise ProviderError("LLM response content was empty")
                return ProviderResponse(
                    content=textual,
                    prompt_tokens=tok_prompt,
                    completion_tokens=tok_comp,
                )

            # Echo the assistant's tool-call request into the history so the
            # following role:tool messages can reference it by tool_call_id.
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                }
            )

            for tc in message.tool_calls:
                name = (getattr(tc.function, "name", "") or "").strip() if tc.function else ""
                raw_args = (getattr(tc.function, "arguments", "") or "{}") if tc.function else "{}"

                try:
                    args = _json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                except (ValueError, TypeError) as e:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"Error: could not parse arguments for tool '{name}': {e}. Do not retry this call.",
                        }
                    )
                    continue

                tool_fn = tool_registry.get(name)
                if tool_fn is None:
                    # Hallucinated tool name — tell the model, never execute.
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": (
                                f"Error: unknown tool '{name}'. "
                                f"Available tools: {sorted(tool_registry.keys())}"
                            ),
                        }
                    )
                    continue

                if on_tool_call is not None:
                    await on_tool_call(name, args)

                try:
                    result = tool_fn(**args)
                except TypeError as e:
                    result = f"Error: invalid arguments for tool '{name}': {e}. Do not retry this call."
                except Exception as e:
                    result = f"Error: tool '{name}' failed: {type(e).__name__}: {e}"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result),
                    }
                )

        # Unreachable: the final round runs without tools and must return content.
        raise ProviderError("Tool-calling loop ended without a final answer")