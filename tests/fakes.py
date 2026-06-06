"""Test doubles. The scripted LLM lets us drive the real agent loop end-to-end
(real registry, real sandbox, real pytest) without any API key or network."""
from __future__ import annotations

from typing import Callable, List, Optional

from patchwork.llm.base import AssistantTurn, LLMClient, Message, ToolCall, ToolSpec, Usage


class ScriptedLLM(LLMClient):
    """Returns a pre-programmed sequence of turns, one per ``complete`` call.

    ``react`` (optional) can inspect the running message list and return a turn
    dynamically; otherwise the next scripted turn is popped.
    """

    model = "scripted"

    def __init__(
        self,
        turns: Optional[List[AssistantTurn]] = None,
        react: Optional[Callable[[List[Message]], AssistantTurn]] = None,
    ):
        self._turns = list(turns or [])
        self._react = react
        self.calls = 0

    def complete(self, *, system: str, messages: List[Message], tools: List[ToolSpec]) -> AssistantTurn:
        self.calls += 1
        if self._react is not None:
            return self._react(messages)
        if not self._turns:
            return AssistantTurn(text="done", usage=Usage(1, 1))
        return self._turns.pop(0)


def tool_turn(idx: int, name: str, **arguments) -> AssistantTurn:
    return AssistantTurn(
        tool_calls=[ToolCall(id=f"call-{idx}", name=name, arguments=arguments)],
        usage=Usage(10, 5),
    )


def final_turn(text: str) -> AssistantTurn:
    return AssistantTurn(text=text, usage=Usage(10, 5))
