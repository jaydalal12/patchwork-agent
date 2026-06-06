"""Typed error taxonomy.

Every failure mode the agent can hit has a class here. Callers (the agent
loop, the retry wrapper, the CLI) branch on type, never on string matching.
The split that matters operationally is ``retryable`` vs not: the resilience
layer only retries errors that declare themselves transient.
"""
from __future__ import annotations

from typing import Optional


class PatchworkError(Exception):
    """Root of every error Patchwork raises on purpose."""

    #: Whether the resilience layer should retry the operation that raised this.
    retryable: bool = False


class ConfigError(PatchworkError):
    """Missing/invalid configuration. Never retryable."""


# --- Tool layer ----------------------------------------------------------
class ToolError(PatchworkError):
    """Base for anything that goes wrong executing a tool."""

    def __init__(self, message: str, *, tool: Optional[str] = None):
        super().__init__(message)
        self.tool = tool


class ToolNotFoundError(ToolError):
    """Model asked for a tool name not in the registry."""


class ToolInputError(ToolError):
    """Arguments failed schema validation before the tool ran."""


class ToolExecutionError(ToolError):
    """The tool ran and raised. ``cause`` carries the original exception."""

    def __init__(self, message: str, *, tool: Optional[str] = None, cause: Optional[BaseException] = None):
        super().__init__(message, tool=tool)
        self.cause = cause


# --- External calls ------------------------------------------------------
class ExternalServiceError(PatchworkError):
    """A call to GitHub / an LLM / the network failed."""

    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class RateLimitError(ExternalServiceError):
    """Provider told us to slow down. Retryable, usually with a hint."""

    retryable = True

    def __init__(self, message: str, *, retry_after: Optional[float] = None, status: Optional[int] = None):
        super().__init__(message, status=status)
        self.retry_after = retry_after


class TransientServiceError(ExternalServiceError):
    """5xx / connection reset / timeout. Worth retrying."""

    retryable = True


# --- Agent control flow --------------------------------------------------
class BudgetExceededError(PatchworkError):
    """The agent hit its tool-call or token budget before finishing."""


class TestsStillFailingError(PatchworkError):
    """The agent declared done but the verification run was still red.

    This is a *correctness* guard, not a crash: surfaced so the loop refuses
    to report success on an unverified fix.
    """
