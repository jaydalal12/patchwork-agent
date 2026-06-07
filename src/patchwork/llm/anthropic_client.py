"""Anthropic provider (primary).

Translates the neutral protocol to the Messages API and back, wraps the call
in our rate limiter + retry, and maps SDK exceptions onto our typed errors so
the resilience layer knows what is transient.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from patchwork.errors import ConfigError, ExternalServiceError, RateLimitError, TransientServiceError
from patchwork.llm.base import (
    AssistantTurn,
    LLMClient,
    Message,
    ToolCall,
    ToolSpec,
    Usage,
)
from patchwork.observability import get_logger
from patchwork.resilience import RateLimiter
from patchwork.resilience.retry import BackoffPolicy, retry_call

_log = get_logger("llm.anthropic")


# Anthropic restricts tool names to ^[a-zA-Z0-9_-]{1,64}$ — no dots. Our names
# are "namespace.action", so we swap "." <-> "__" at this boundary only. (No
# tool name contains "__", so the transform is reversible.)
def _san(name: str) -> str:
    return name.replace(".", "__")


def _desan(name: str) -> str:
    return name.replace("__", ".")


# -- pure translation (no SDK needed; unit-tested directly) ----------------
def to_api_messages(messages: List[Message]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.text})
        elif m.role == "assistant":
            content: List[Dict[str, Any]] = []
            if m.text:
                content.append({"type": "text", "text": m.text})
            for tc in m.tool_calls:
                content.append({"type": "tool_use", "id": tc.id, "name": _san(tc.name), "input": tc.arguments})
            out.append({"role": "assistant", "content": content})
        elif m.role == "tool":
            tr = m.tool_result
            assert tr is not None
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tr.tool_call_id,
                            "content": tr.content,
                            "is_error": tr.is_error,
                        }
                    ],
                }
            )
    return out


def to_api_tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    return [{"name": _san(t.name), "description": t.description, "input_schema": t.input_schema} for t in tools]


def parse_response(resp: Any) -> AssistantTurn:
    text_parts: List[str] = []
    calls: List[ToolCall] = []
    for block in resp.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            calls.append(ToolCall(id=block.id, name=_desan(block.name), arguments=dict(block.input)))
    return AssistantTurn(
        text="".join(text_parts),
        tool_calls=calls,
        usage=Usage(
            input_tokens=getattr(resp.usage, "input_tokens", 0),
            output_tokens=getattr(resp.usage, "output_tokens", 0),
        ),
        stop_reason=resp.stop_reason or "",
    )


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str, model: str, *, max_tokens: int = 4096):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise ConfigError(
                "anthropic SDK not installed. Install with: pip install 'patchwork[anthropic]'"
            ) from e
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self._max_tokens = max_tokens
        # Anthropic default tier allows generous RPM; keep a sane client-side cap.
        self._limiter = RateLimiter(rate=5.0, burst=10)

    # -- error mapping ----------------------------------------------------
    def _call(self, **kwargs):
        a = self._anthropic
        try:
            return self._client.messages.create(**kwargs)
        except a.RateLimitError as e:
            retry_after = None
            try:
                retry_after = float(e.response.headers.get("retry-after"))  # type: ignore[union-attr]
            except Exception:
                pass
            raise RateLimitError("anthropic rate limit", retry_after=retry_after, status=429) from e
        except a.APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status and 500 <= status < 600:
                raise TransientServiceError(f"anthropic {status}", status=status) from e
            raise ExternalServiceError(f"anthropic error {status}: {str(e)[:240]}", status=status) from e
        except a.APIConnectionError as e:
            raise TransientServiceError("anthropic connection error") from e

    # -- public -----------------------------------------------------------
    def complete(self, *, system: str, messages: List[Message], tools: List[ToolSpec]) -> AssistantTurn:
        self._limiter.acquire()
        resp = retry_call(
            lambda: self._call(
                model=self.model,
                max_tokens=self._max_tokens,
                system=system,
                tools=to_api_tools(tools),
                messages=to_api_messages(messages),
            ),
            policy=BackoffPolicy(max_attempts=4),
            op_name="anthropic.messages.create",
        )
        return parse_response(resp)

    def count_tokens(self, text: str) -> int:
        try:
            r = self._client.messages.count_tokens(
                model=self.model, messages=[{"role": "user", "content": text}]
            )
            return int(r.input_tokens)
        except Exception:
            return super().count_tokens(text)
