"""The neutral protocol every provider implements.

A conversation is a list of :class:`Message`. The model replies with an
:class:`AssistantTurn` that is *either* final text *or* one-plus tool calls
(or both). Tool results are appended as ``Message(role="tool", ...)`` and the
loop calls :meth:`LLMClient.complete` again. This shape is the common
denominator of the Anthropic and Gemini tool-use APIs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

Role = Literal["user", "assistant", "tool"]


@dataclass
class ToolSpec:
    """A tool advertised to the model. ``input_schema`` is JSON Schema."""

    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: Role
    # For user/assistant: free text. For assistant tool turns: may be "".
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    # Only set when role == "tool".
    tool_result: Optional[ToolResult] = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def estimate_tokens(text: str) -> int:
    """Cheap, provider-independent token estimate (~4 chars/token).

    Used by the context manager for budgeting; not billing-accurate, and that
    is fine — it only needs to be monotonic and roughly linear.
    """
    return max(1, len(text) // 4)


def estimate_message_tokens(msg: Message) -> int:
    n = estimate_tokens(msg.text)
    for tc in msg.tool_calls:
        n += estimate_tokens(tc.name) + estimate_tokens(json.dumps(tc.arguments))
    if msg.tool_result:
        n += estimate_tokens(msg.tool_result.content)
    return n


class LLMClient:
    """Abstract provider. Implementations live alongside this file."""

    model: str

    def complete(
        self,
        *,
        system: str,
        messages: List[Message],
        tools: List[ToolSpec],
    ) -> AssistantTurn:
        raise NotImplementedError

    # Providers may override with a native counter; default is the estimate.
    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)
