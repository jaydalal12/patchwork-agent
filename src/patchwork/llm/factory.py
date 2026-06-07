"""Build the configured LLM client from :class:`Settings`."""
from __future__ import annotations

from patchwork.config import Settings
from patchwork.errors import ConfigError
from patchwork.llm.base import LLMClient
from patchwork.observability import get_logger

_log = get_logger("llm.factory")


def build_llm(settings: Settings) -> LLMClient:
    provider = settings.resolved_provider()
    _log.info("selected llm provider", provider=provider)

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ConfigError("provider=anthropic but ANTHROPIC_API_KEY is unset")
        from patchwork.llm.anthropic_client import AnthropicClient

        return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model)

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ConfigError("provider=gemini but GEMINI_API_KEY is unset")
        from patchwork.llm.gemini_client import GeminiClient

        return GeminiClient(settings.gemini_api_key, settings.gemini_model, rpm=settings.gemini_rpm)

    raise ConfigError(f"unsupported provider {provider!r}")
