"""Provider abstraction layer for LLM calls.

All providers must implement the `AbstractLLMProvider` interface.
The default implementation uses NVIDIA NIM via an OpenAI-compatible client.
"""

from __future__ import annotations

import abc
import os
from typing import Any

from pydantic import BaseModel, Field


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
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

        import openai

        # Provider‑agnostic env vars: any .env setting these will work
        # across OpenAI, Gemini, OpenRouter, or local OpenAI‑compatible hosts.
        raw_key = os.getenv("LLM_API_KEY", "")
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
        # Ensure max_tokens is at least 8192 to prevent truncation
        configured_max_tok = kwargs.get("max_tokens", self.config.max_tokens)
        max_tok = max(configured_max_tok, 8192)

        # Enable JSON mode when a schema is provided (works with OpenAI‑compatible endpoints)
        # Some endpoints may not support response_format, so we wrap it in a try/except
        response_format = {"type": "json_object"} if schema else None

        request_kwargs = {
            "model": self.config.model_id,
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
            response = self.client.chat.completions.create(**request_kwargs)

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

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict = None,
        **kwargs: Any,
    ) -> ProviderResponse:

        temp = kwargs.get("temperature", self.config.temperature)
        # Ensure max_tokens is at least 8192 to prevent truncation
        configured_max_tok = kwargs.get("max_tokens", self.config.max_tokens)
        max_tok = max(configured_max_tok, 8192)

        # Enable JSON mode when a schema is provided (works with OpenAI-compatible endpoints)
        # Some endpoints may not support response_format, so we wrap it in a try/except
        response_format = {"type": "json_object"} if schema else None

        request_kwargs = {
            "model": self.config.model_id,
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
            response = self.client.chat.completions.create(**request_kwargs)

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
            # Re-raise ProviderError as-is (no prefix wrapping)
            raise
        except Exception as e:
            raise ProviderError(
                f"LLM provider request failed: {str(e)}"
            )