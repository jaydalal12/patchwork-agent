"""The control loop.

A turn is: ask the model → if it wants tools, run them, feed results back,
repeat → if it produces text with no tool calls, that is the final answer.
Between turns we compact context and enforce the tool-call budget. The loop is
deliberately small; all domain behavior lives in tools and the system prompt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from patchwork.agent.context import ConversationContext
from patchwork.errors import BudgetExceededError, ToolNotFoundError
from patchwork.llm.base import AssistantTurn, LLMClient, ToolResult
from patchwork.observability import get_logger
from patchwork.registry import ToolRegistry
from patchwork.tools.base import ToolContext

_log = get_logger("agent.loop")


@dataclass
class AgentResult:
    final_text: str
    tool_calls: int
    turns: int
    input_tokens: int = 0
    output_tokens: int = 0
    stopped_reason: str = "completed"
    ledger: List[str] = field(default_factory=list)


def run_agent(
    *,
    llm: LLMClient,
    registry: ToolRegistry,
    tool_ctx: ToolContext,
    conversation: ConversationContext,
    max_tool_calls: int,
    on_step: Optional[Callable[[int, AssistantTurn], None]] = None,
) -> AgentResult:
    specs = registry.specs()
    tool_calls = 0
    turns = 0
    in_tok = out_tok = 0

    while True:
        conversation.compact_if_needed()
        turns += 1
        with tool_ctx.tracer.span(f"llm.turn.{turns}", "llm"):
            turn = llm.complete(
                system=conversation.system_with_ledger(),
                messages=conversation.messages,
                tools=specs,
            )
        in_tok += turn.usage.input_tokens
        out_tok += turn.usage.output_tokens
        conversation.add_assistant(turn)
        _log.info(
            "assistant turn",
            turn=turns,
            stop_reason=turn.stop_reason,
            has_text=bool(turn.text),
            n_tool_calls=len(turn.tool_calls),
        )
        if on_step:
            on_step(turns, turn)

        if not turn.wants_tools:
            if not turn.text:
                _log.warning("agent produced no text and no tool calls", stop_reason=turn.stop_reason)
            _log.info("agent finished", turns=turns, tool_calls=tool_calls)
            return AgentResult(
                final_text=turn.text,
                tool_calls=tool_calls,
                turns=turns,
                input_tokens=in_tok,
                output_tokens=out_tok,
                ledger=list(conversation._ledger),
            )

        for call in turn.tool_calls:
            if tool_calls >= max_tool_calls:
                conversation.add_tool_result(
                    ToolResult(
                        tool_call_id=call.id,
                        name=call.name,
                        content="ERROR: tool-call budget exhausted; wrap up and report what you have.",
                        is_error=True,
                    )
                )
                raise BudgetExceededError(
                    f"exceeded max_tool_calls={max_tool_calls}"
                )
            tool_calls += 1
            try:
                result = registry.execute(call.name, call.arguments, tool_ctx)
            except ToolNotFoundError as e:
                result = ToolResult(
                    tool_call_id=call.id, name=call.name, content=f"ERROR: {e}", is_error=True
                )
            result.tool_call_id = call.id
            conversation.add_tool_result(result)
            conversation.note(
                f"{call.name}({_brief_args(call.arguments)}) -> "
                f"{'error' if result.is_error else 'ok'}"
            )
            _log.info(
                "tool executed",
                tool=call.name,
                ok=not result.is_error,
                n=tool_calls,
            )


def _brief_args(args: dict) -> str:
    parts = []
    for k, v in list(args.items())[:3]:
        s = str(v)
        parts.append(f"{k}={s[:40]}")
    return ", ".join(parts)
