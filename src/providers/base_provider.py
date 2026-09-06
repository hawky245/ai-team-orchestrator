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
    """Provider that talks to NVIDIA NIM via an OpenAI-compatible client."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

        import openai

        base_url = os.getenv(
            "NIM_BASE_URL",
            "https://integrate.api.nvidia.com/v1",
        )

        api_key = os.getenv("NVIDIA_API_KEY", "")

        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
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

        # NVIDIA NIM doesn't support guided_json or strict response_format
        # We'll rely on system prompts and shared JSON parser instead
        response_format = None

        request_kwargs = {
            "model": self.config.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temp,
            "max_tokens": max_tok,
            # Remove response_format as NIM may not support it properly
            # "response_format": response_format,
        }

        try:
            response = self.client.chat.completions.create(**request_kwargs)

            choice = response.choices[0]
            textual = choice.message.content or ""

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

        except Exception as e:
            raise ProviderError(
                f"NVIDIA NIM request failed: {str(e)}"
            )