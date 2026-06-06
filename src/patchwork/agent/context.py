"""Conversation context with an explicit, in-code compaction strategy.

Long-horizon runs blow the context window unless something actively manages
it. Our strategy, deterministic and dependency-free:

* **Pin** the task (first user message) and a *progress ledger* the agent
  appends to — these never get compacted, so the plan survives.
* **Keep recent turns verbatim** (the last ``keep_recent`` messages) — local
  reasoning needs full fidelity.
* **Compact the middle**: when the estimated token count exceeds the budget,
  oldest-first, replace bulky tool-result bodies with a short stub recording
  the tool, size, and a head excerpt. The fact a call happened and roughly
  what it returned is preserved; the kilobytes are not.

This is intentionally not "summarize with the LLM" by default — a mechanical
strategy is reproducible, free, and can't hallucinate away a fact the agent
later needs. An optional LLM summarizer can be layered on top.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from patchwork.llm.base import Message, ToolResult, estimate_message_tokens
from patchwork.observability import get_logger

_log = get_logger("agent.context")

_STUB_HEAD = 240  # chars of a compacted tool result we keep as an excerpt


@dataclass
class ConversationContext:
    system: str
    token_budget: int
    keep_recent: int = 8
    messages: List[Message] = field(default_factory=list)
    _ledger: List[str] = field(default_factory=list)
    _compacted: set = field(default_factory=set)  # ids of messages already stubbed

    # -- building the transcript -----------------------------------------
    def add_user(self, text: str) -> None:
        self.messages.append(Message(role="user", text=text))

    def add_assistant(self, turn) -> None:  # turn: AssistantTurn
        self.messages.append(
            Message(role="assistant", text=turn.text, tool_calls=list(turn.tool_calls))
        )

    def add_tool_result(self, result: ToolResult) -> None:
        self.messages.append(Message(role="tool", tool_result=result))

    def note(self, line: str) -> None:
        """Append to the pinned progress ledger (survives compaction)."""
        self._ledger.append(line)

    # -- budgeting --------------------------------------------------------
    def estimated_tokens(self) -> int:
        base = estimate_message_tokens(Message(role="user", text=self.system))
        base += estimate_message_tokens(Message(role="user", text=self._ledger_text()))
        return base + sum(estimate_message_tokens(m) for m in self.messages)

    def _ledger_text(self) -> str:
        if not self._ledger:
            return ""
        return "PROGRESS LEDGER (pinned):\n" + "\n".join(f"- {l}" for l in self._ledger)

    def system_with_ledger(self) -> str:
        led = self._ledger_text()
        return f"{self.system}\n\n{led}" if led else self.system

    def compact_if_needed(self) -> bool:
        """Bring the transcript under budget. Returns True if it compacted."""
        if self.estimated_tokens() <= self.token_budget:
            return False

        # Indices we must not touch: the first user message (the task) and the
        # last ``keep_recent`` messages.
        n = len(self.messages)
        pinned_head = next((i for i, m in enumerate(self.messages) if m.role == "user"), 0)
        protected_tail = set(range(max(0, n - self.keep_recent), n))

        compacted_any = False
        for i, m in enumerate(self.messages):
            if self.estimated_tokens() <= self.token_budget:
                break
            if i == pinned_head or i in protected_tail:
                continue
            if m.role == "tool" and m.tool_result and id(m) not in self._compacted:
                body = m.tool_result.content
                if len(body) > _STUB_HEAD:
                    head = body[:_STUB_HEAD].replace("\n", " ")
                    m.tool_result = ToolResult(
                        tool_call_id=m.tool_result.tool_call_id,
                        name=m.tool_result.name,
                        content=f"[compacted {len(body)} chars] {head}…",
                        is_error=m.tool_result.is_error,
                    )
                    self._compacted.add(id(m))
                    compacted_any = True

        if compacted_any:
            _log.info(
                "compacted context",
                est_tokens=self.estimated_tokens(),
                budget=self.token_budget,
                compacted=len(self._compacted),
            )
        return compacted_any
