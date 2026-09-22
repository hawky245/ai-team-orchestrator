"""Provider abstraction layer for LLM calls.

All providers must implement the `AbstractLLMProvider` interface.
The default implementation uses NVIDIA NIM via an OpenAI-compatible client.
"""

from __future__ import annotations

import abc
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


class ProviderUsage(BaseModel):
    """Token usage information returned by a provider."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ProviderResponse(BaseModel):
    """Normalized response from any provider."""
    content: str
    usage: ProviderUsage = ProviderUsage()
    finish_reason: str | None = None


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


class AbstractLLMProvider(abc.ABC):
    """Interface that every LLM provider must implement."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass

    @abc.abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict = None,
        **kwargs: Any,
    ) -> ProviderResponse:
        pass


class NvidiaNimProvider(AbstractLLMProvider):
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

        # Initialise client with the first key; we'll rotate per request.
        client_kwargs: dict = {"api_key": self.api_keys[0]}
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = openai.OpenAI(**client_kwargs)

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

    async def _create_completion(self, model: str, request_kwargs: dict):
        """Perform the HTTP call in a worker thread.

        The bundled OpenAI client is synchronous; calling it directly would
        block the event loop for the whole round-trip, silently serializing
        "concurrent" workers and delaying WebSocket frames. to_thread keeps
        the loop free so waves truly overlap.
        """
        return await asyncio.to_thread(
            self.client.chat.completions.create,
            **{**request_kwargs, "model": model},
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
            # Re‑initialise client with the next key – cheap because OpenAI client
            # only stores the key in its config.
            self.client = openai.OpenAI(api_key=self._next_api_key(), **({"base_url": os.getenv("LLM_BASE_URL")} if os.getenv("LLM_BASE_URL") else {}))

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

            usage_data = getattr(response, "usage", None)

            if usage_data:
                prompt_tok = getattr(
                    usage_data,
                    "prompt_tokens",
                    0,
                )
                comp_tok = getattr(
                    usage_data,
                    "completion_tokens",
                    0,
                )
            else:
                prompt_tok = 0
                comp_tok = 0

            return ProviderResponse(
                content=textual,
                usage=ProviderUsage(
                    prompt_tokens=prompt_tok,
                    completion_tokens=comp_tok,
                    total_tokens=prompt_tok + comp_tok,
                ),
                finish_reason=choice.finish_reason,
            )

        except ProviderError:
            # Re‑raise ProviderError as‑is (no prefix wrapping)
            raise
        except Exception as e:
            raise ProviderError(
                f"LLM provider request failed: {str(e)}"
            )

    @property
    def name(self) -> str:
        return "nvidia_nim"

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
        total_prompt = 0
        total_comp = 0

        for round_no in range(self.MAX_TOOL_ROUNDS + 1):
            request_kwargs = {
                "model": effective_model,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": max(self.config.max_tokens, 8192),
                "tools": tools,
                "tool_choice": "auto",
            }
            if round_no == self.MAX_TOOL_ROUNDS:
                # Tool budget exhausted — force a final synthesized answer.
                request_kwargs.pop("tools")
                request_kwargs.pop("tool_choice")

            try:
                response = await self._create_with_backoff(
                    request_kwargs, on_retry, model=effective_model
                )
            except ProviderError:
                # Keep ModelExhaustedError's structured fallback state intact.
                raise
            except Exception as e:
                raise ProviderError(f"LLM provider request failed: {str(e)}")

            if not response.choices:
                raise ProviderError("LLM response had no choices")

            choice = response.choices[0]
            usage_data = getattr(response, "usage", None)
            if usage_data:
                total_prompt += getattr(usage_data, "prompt_tokens", 0) or 0
                total_comp += getattr(usage_data, "completion_tokens", 0) or 0

            message = choice.message

            if not getattr(message, "tool_calls", None):
                textual = message.content or ""
                if not textual.strip():
                    raise ProviderError("LLM response content was empty")
                return ProviderResponse(
                    content=textual,
                    usage=ProviderUsage(
                        prompt_tokens=total_prompt,
                        completion_tokens=total_comp,
                        total_tokens=total_prompt + total_comp,
                    ),
                    finish_reason=choice.finish_reason,
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