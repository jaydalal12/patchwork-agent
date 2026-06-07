"""Central, typed configuration loaded from the environment.

One ``Settings`` object is threaded through the app rather than reading
``os.environ`` ad hoc, so tests can construct an explicit config and the
deployment surface (env vars) lives in exactly one place.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

Provider = Literal["anthropic", "groq", "gemini"]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    """Resolved runtime configuration. Immutable once built."""

    model_config = {"frozen": True}

    provider: Optional[Provider] = None
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-opus-4-8"
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.5-pro"
    # Requests/min budget for the client-side limiter. Match your tier:
    # free 2.5-flash ~10, free 2.0-flash-lite ~30, paid much higher.
    gemini_rpm: int = Field(default=10, ge=1)

    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_rpm: int = Field(default=30, ge=1)

    github_token: Optional[str] = None

    max_tool_calls: int = Field(default=60, ge=1)
    context_token_budget: int = Field(default=120_000, ge=10_000)

    sandbox_root: Path = Field(default_factory=lambda: Path(".patchwork_sandbox"))
    log_level: str = "INFO"
    log_json: bool = False

    def resolved_provider(self) -> Provider:
        """Pick the provider: explicit override, else whichever key exists.

        Anthropic is preferred when both are present.
        """
        if self.provider:
            return self.provider
        if self.anthropic_api_key:
            return "anthropic"
        if self.groq_api_key:
            return "groq"
        if self.gemini_api_key:
            return "gemini"
        raise ConfigError(
            "No LLM provider available: set ANTHROPIC_API_KEY, GROQ_API_KEY, or "
            "GEMINI_API_KEY (or PATCHWORK_LLM_PROVIDER explicitly)."
        )

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.getenv("PATCHWORK_LLM_PROVIDER") or None
        if provider not in (None, "anthropic", "groq", "gemini"):
            raise ConfigError(f"Unknown PATCHWORK_LLM_PROVIDER={provider!r}")
        return cls(
            provider=provider,  # type: ignore[arg-type]
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            anthropic_model=os.getenv("PATCHWORK_ANTHROPIC_MODEL", "claude-opus-4-8"),
            gemini_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            gemini_model=os.getenv("PATCHWORK_GEMINI_MODEL", "gemini-2.5-pro"),
            gemini_rpm=int(os.getenv("PATCHWORK_GEMINI_RPM", "10")),
            groq_api_key=os.getenv("GROQ_API_KEY"),
            groq_model=os.getenv("PATCHWORK_GROQ_MODEL", "llama-3.3-70b-versatile"),
            groq_rpm=int(os.getenv("PATCHWORK_GROQ_RPM", "30")),
            github_token=os.getenv("GITHUB_TOKEN"),
            max_tool_calls=int(os.getenv("PATCHWORK_MAX_TOOL_CALLS", "60")),
            context_token_budget=int(os.getenv("PATCHWORK_CONTEXT_TOKEN_BUDGET", "120000")),
            sandbox_root=Path(os.getenv("PATCHWORK_SANDBOX_ROOT", ".patchwork_sandbox")),
            log_level=os.getenv("PATCHWORK_LOG_LEVEL", "INFO"),
            log_json=_env_bool("PATCHWORK_LOG_JSON", False),
        )


# Imported here (not at top) to avoid a circular import with errors.py at module load.
from patchwork.errors import ConfigError  # noqa: E402
