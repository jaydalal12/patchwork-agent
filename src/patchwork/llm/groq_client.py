"""Groq provider — fast, generous free tier, OpenAI-style tool calling.

Groq's chat-completions API mirrors OpenAI: the system prompt is a message with
role ``system``, tool calls come back on ``message.tool_calls`` with arguments
as a JSON *string*, and tool results are messages with role ``tool`` keyed by
``tool_call_id``. Translation/parse are pure functions so they unit-test without
the SDK or network.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from patchwork.errors import ConfigError, ExternalServiceError, RateLimitError, TransientServiceError
from patchwork.llm.base import AssistantTurn, LLMClient, Message, ToolCall, ToolSpec, Usage
from patchwork.observability import get_logger
from patchwork.resilience import RateLimiter
from patchwork.resilience.retry import BackoffPolicy, retry_call

_log = get_logger("llm.groq")


# -- pure translation (no SDK needed; unit-tested directly) ----------------
def to_messages(system: str, messages: List[Message]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.text})
        elif m.role == "assistant":
            msg: Dict[str, Any] = {"role": "assistant", "content": m.text or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ]
            out.append(msg)
        elif m.role == "tool":
            tr = m.tool_result
            assert tr is not None
            out.append({"role": "tool", "tool_call_id": tr.tool_call_id, "name": tr.name, "content": tr.content})
    return out


def to_tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
        for t in tools
    ]


def parse_response(resp: Any) -> AssistantTurn:
    choice = resp.choices[0]
    msg = choice.message
    calls: List[ToolCall] = []
    for tc in (getattr(msg, "tool_calls", None) or []):
        try:
            args = json.loads(tc.function.arguments or "{}")
        except (ValueError, TypeError):
            args = {}
        if not isinstance(args, dict):  # model can emit "null" / a bare list
            args = {}
        calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
    usage = getattr(resp, "usage", None)
    return AssistantTurn(
        text=msg.content or "",
        tool_calls=calls,
        usage=Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        ),
        stop_reason=choice.finish_reason or "",
    )


class GroqClient(LLMClient):
    def __init__(self, api_key: str, model: str, *, max_tokens: int = 1500, rpm: int = 30):
        try:
            import groq
        except ImportError as e:  # pragma: no cover
            raise ConfigError("groq SDK not installed. Install with: pip install 'patchwork[groq]'") from e
        self._groq = groq
        self._client = groq.Groq(api_key=api_key)
        self.model = model
        # Groq counts max_tokens toward the per-request token cap (free tier
        # ~8k), so keep the completion reservation small — tool-call responses
        # and the final report are short anyway.
        self._max_tokens = max_tokens
        rate = max(0.1, (rpm * 0.9) / 60.0)
        self._limiter = RateLimiter(rate=rate, burst=max(2, rpm // 6))

    def _call(self, **kwargs):
        g = self._groq
        try:
            return self._client.chat.completions.create(**kwargs)
        except g.RateLimitError as e:
            retry_after = None
            try:
                retry_after = float(e.response.headers.get("retry-after"))  # type: ignore[union-attr]
            except Exception:
                pass
            raise RateLimitError("groq rate limit", retry_after=retry_after, status=429) from e
        except g.APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status and 500 <= status < 600:
                raise TransientServiceError(f"groq {status}", status=status) from e
            raise ExternalServiceError(f"groq error {status}", status=status) from e
        except g.APIConnectionError as e:
            raise TransientServiceError("groq connection error") from e

    def complete(self, *, system: str, messages: List[Message], tools: List[ToolSpec]) -> AssistantTurn:
        self._limiter.acquire()
        resp = retry_call(
            lambda: self._call(
                model=self.model,
                max_tokens=self._max_tokens,
                messages=to_messages(system, messages),
                tools=to_tools(tools),
                tool_choice="auto",
            ),
            policy=BackoffPolicy(max_attempts=5, base_delay=2.0, max_delay=45.0),
            op_name="groq.chat.completions.create",
        )
        return parse_response(resp)
