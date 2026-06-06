"""Google Gemini provider (fallback).

Same neutral protocol, mapped onto google-generativeai's function-calling.
Gemini uses roles ``user``/``model`` and represents tool calls as
``function_call`` parts and tool results as ``function_response`` parts.
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

_log = get_logger("llm.gemini")

# Gemini is our free-tier fallback path. Free tier enforces a tight per-minute
# request budget, so we (a) pace requests well under it and (b) back off
# patiently enough to ride out a full one-minute window if we still get 429.
GEMINI_RETRY = BackoffPolicy(max_attempts=6, base_delay=5.0, max_delay=60.0, multiplier=2.0)


def _clean_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Gemini's FunctionDeclaration accepts a subset of JSON Schema.

    Strip keys it rejects (``additionalProperties``, ``$schema``, ``default``)
    and recurse into nested object/array schemas.
    """
    drop = {"additionalProperties", "$schema", "default", "examples", "title"}
    out: Dict[str, Any] = {}
    for k, v in schema.items():
        if k in drop:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: _clean_schema(pv) for pk, pv in v.items()}
        elif k == "items" and isinstance(v, dict):
            out[k] = _clean_schema(v)
        else:
            out[k] = v
    return out


# -- pure translation (no SDK needed; unit-tested directly) ----------------
def to_contents(messages: List[Message]) -> List[Dict[str, Any]]:
    contents: List[Dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            contents.append({"role": "user", "parts": [{"text": m.text}]})
        elif m.role == "assistant":
            parts: List[Dict[str, Any]] = []
            if m.text:
                parts.append({"text": m.text})
            for tc in m.tool_calls:
                parts.append({"function_call": {"name": tc.name, "args": tc.arguments}})
            contents.append({"role": "model", "parts": parts})
        elif m.role == "tool":
            tr = m.tool_result
            assert tr is not None
            payload: Dict[str, Any] = {"error": tr.content} if tr.is_error else {"result": tr.content}
            contents.append(
                {"role": "user", "parts": [{"function_response": {"name": tr.name, "response": payload}}]}
            )
    return contents


def to_tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    return [
        {
            "function_declarations": [
                {"name": t.name, "description": t.description, "parameters": _clean_schema(t.input_schema)}
                for t in tools
            ]
        }
    ]


def parse_response(resp: Any) -> AssistantTurn:
    text_parts: List[str] = []
    calls: List[ToolCall] = []
    idx = 0
    candidate = resp.candidates[0] if resp.candidates else None
    if candidate:
        for part in candidate.content.parts:
            fn = getattr(part, "function_call", None)
            if fn and fn.name:
                calls.append(ToolCall(id=f"gemini-{idx}", name=fn.name, arguments=dict(fn.args) if fn.args else {}))
                idx += 1
            elif getattr(part, "text", None):
                text_parts.append(part.text)
    usage = getattr(resp, "usage_metadata", None)
    return AssistantTurn(
        text="".join(text_parts),
        tool_calls=calls,
        usage=Usage(
            input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
        ),
        stop_reason=str(getattr(candidate, "finish_reason", "")) if candidate else "",
    )


class GeminiClient(LLMClient):
    def __init__(self, api_key: str, model: str, *, max_output_tokens: int = 4096):
        try:
            import google.generativeai as genai
        except ImportError as e:  # pragma: no cover
            raise ConfigError(
                "google-generativeai not installed. Install with: pip install 'patchwork[gemini]'"
            ) from e
        self._genai = genai
        genai.configure(api_key=api_key)
        self.model = model
        self._max_output_tokens = max_output_tokens
        # ~1 request / 5s, no bursting — keeps under free-tier per-minute limits.
        self._limiter = RateLimiter(rate=0.2, burst=2)

    def _call(self, system: str, contents, tools):
        genai = self._genai
        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system,
            tools=tools,
            generation_config={"max_output_tokens": self._max_output_tokens},
        )
        try:
            return model.generate_content(contents)
        except Exception as e:  # google SDK raises a wide variety; classify by text/attrs
            status = getattr(e, "code", None) or getattr(e, "status_code", None)
            msg = str(e).lower()
            if "429" in msg or "resource_exhausted" in msg or "rate" in msg:
                raise RateLimitError("gemini rate limit", status=429) from e
            if any(s in msg for s in ("500", "503", "internal", "unavailable", "deadline")):
                raise TransientServiceError("gemini transient error") from e
            raise ExternalServiceError(f"gemini error: {e}", status=status) from e

    # -- public -----------------------------------------------------------
    def complete(self, *, system: str, messages: List[Message], tools: List[ToolSpec]) -> AssistantTurn:
        self._limiter.acquire()
        resp = retry_call(
            lambda: self._call(system, to_contents(messages), to_tools(tools)),
            policy=GEMINI_RETRY,
            op_name="gemini.generate_content",
        )
        return parse_response(resp)
