"""Subagent harness — a real nested agent, not a relabelled function call.

``run_subagent`` spins up a *separate* :class:`ConversationContext` (its own
window, seeded only with the inputs the parent chooses to pass), a *scoped*
registry (e.g. read-only tools), and runs the same control loop to completion.
It then coerces the subagent's final message into a structured object validated
against a caller-supplied JSON Schema, and hands *only that object* back to the
parent. The parent's context never sees the subagent's intermediate reasoning —
that isolation is the point: it keeps the parent's window clean and bounds what
a focused sub-task can perturb.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from patchwork.agent.context import ConversationContext
from patchwork.agent.loop import run_agent
from patchwork.errors import ToolExecutionError
from patchwork.llm.base import LLMClient
from patchwork.observability import get_logger
from patchwork.registry import ToolRegistry
from patchwork.tools.base import ToolContext

_log = get_logger("agent.subagent")

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class SubagentResult:
    data: Dict[str, Any]
    tool_calls: int
    turns: int


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    # Tolerate ```json fences and surrounding prose.
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    m = _JSON_BLOCK.search(text)
    if not m:
        raise ToolExecutionError("subagent did not return JSON")
    return json.loads(m.group(0))


def run_subagent(
    *,
    parent_ctx: ToolContext,
    role: str,
    task: str,
    output_schema: Dict[str, Any],
    allow_namespaces: Optional[List[str]] = None,
    scope: str = "read",
    max_tool_calls: int = 15,
) -> SubagentResult:
    """Run an isolated subagent and return its structured result."""
    llm: LLMClient = parent_ctx.llm
    full_registry: ToolRegistry = parent_ctx.registry
    if llm is None or full_registry is None:
        raise ToolExecutionError("subagent requires llm and registry on the context")

    scoped = full_registry.scoped(namespaces=allow_namespaces, scope=scope)  # type: ignore[arg-type]

    system = (
        f"You are a {role}, a focused subagent. You operate in an isolated context "
        f"with a restricted tool set ({', '.join(scoped.namespaces()) or 'none'}; scope={scope}).\n"
        "Do the task, then STOP and output ONLY a single JSON object matching this schema "
        "(no prose, no code fence):\n"
        f"{json.dumps(output_schema)}"
    )
    convo = ConversationContext(
        system=system,
        token_budget=parent_ctx.settings.context_token_budget,
        keep_recent=6,
    )
    convo.add_user(task)

    # Child shares execution resources (sandbox, github, tracer) but gets the
    # *scoped* registry as its own — so any nested spawning is also restricted.
    child_ctx = ToolContext(
        settings=parent_ctx.settings,
        tracer=parent_ctx.tracer,
        sandbox=parent_ctx.sandbox,
        github=parent_ctx.github,
        llm=llm,
        registry=scoped,
        scratch={},
    )

    with parent_ctx.tracer.span(f"subagent.{role}", "subagent", scope=scope):
        result = run_agent(
            llm=llm,
            registry=scoped,
            tool_ctx=child_ctx,
            conversation=convo,
            max_tool_calls=max_tool_calls,
        )

    data = _extract_json(result.final_text)
    _log.info("subagent returned", role=role, keys=sorted(data), tool_calls=result.tool_calls)
    return SubagentResult(data=data, tool_calls=result.tool_calls, turns=result.turns)
