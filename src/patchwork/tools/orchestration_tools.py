"""``orchestration.*`` tools — each spawns a real, isolated subagent.

These are the property-2 surface. ``analyze_failure`` also demonstrates
composition (property 5): it consumes the structured failure objects produced
by ``ci.run_tests`` / ``ci.parse_failures`` and feeds them to a subagent.

The subagent runs in its own context with a *scoped* registry (read-only here),
so the analyst literally cannot mutate the repo, and its multi-step reasoning
never pollutes the parent's window — only its structured verdict returns.
"""
from __future__ import annotations

import json

from patchwork.agent.subagent import run_subagent
from patchwork.errors import ToolExecutionError
from patchwork.tools.base import ToolContext, tool

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string", "description": "one-sentence diagnosis"},
        "suspect_files": {"type": "array", "items": {"type": "string"}},
        "fix_strategy": {"type": "string", "description": "concrete steps to fix"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["root_cause", "suspect_files", "fix_strategy", "confidence"],
}

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "approve": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["approve", "issues", "risk"],
}


@tool(
    namespace="orchestration",
    scope="read",
    descriptions={"failing_test": "pytest node id of the failing test", "error_excerpt": "the assertion/traceback text"},
)
def analyze_failure(ctx: ToolContext, failing_test: str, error_excerpt: str = "") -> dict:
    """Spawn a read-only analyst subagent to root-cause a failing test.

    Hands the failing test (and optional error text) to an isolated subagent
    with only read tools (ci + code). Returns a structured diagnosis the parent
    can act on. Composes on the structured failures from ci.run_tests.
    """
    task = (
        f"A test is failing: {failing_test}\n"
        f"Error excerpt:\n{error_excerpt or '(none provided — get it via ci.failure_detail)'}\n\n"
        "Investigate using read-only tools (read the test, read the implementation, "
        "inspect the traceback). Do NOT attempt to edit anything. Determine the root "
        "cause and a concrete fix strategy, then return the JSON verdict."
    )
    res = run_subagent(
        parent_ctx=ctx,
        role="test failure analyst",
        task=task,
        output_schema=_ANALYSIS_SCHEMA,
        allow_namespaces=["ci", "code"],
        scope="read",
        max_tool_calls=12,
    )
    return {"analysis": res.data, "subagent_tool_calls": res.tool_calls}


@tool(namespace="orchestration", scope="read", descriptions={"context_hint": "what the change was meant to do"})
def review_patch(ctx: ToolContext, context_hint: str = "") -> dict:
    """Spawn a read-only reviewer subagent to critique the current working diff.

    The reviewer sees the diff (via git read tools) and the surrounding code,
    then returns an approval decision plus concrete issues. Used as a gate
    before opening a PR.
    """
    if ctx.sandbox is None:
        raise ToolExecutionError("no sandbox on context")
    diff = ctx.sandbox.run_git(["diff", "HEAD"]).stdout or "(no committed changes; checking working tree)"
    if diff.startswith("(no committed"):
        diff = ctx.sandbox.run_git(["diff"]).stdout or "(empty diff)"
    task = (
        f"Review this change. Intent: {context_hint or 'fix failing tests'}.\n\n"
        f"DIFF:\n{diff[:6000]}\n\n"
        "Use read-only tools to inspect surrounding code if needed. Judge correctness, "
        "scope creep, and risk. Return the JSON verdict."
    )
    res = run_subagent(
        parent_ctx=ctx,
        role="code reviewer",
        task=task,
        output_schema=_REVIEW_SCHEMA,
        allow_namespaces=["code", "git"],
        scope="read",
        max_tool_calls=10,
    )
    return {"review": res.data, "subagent_tool_calls": res.tool_calls}


@tool(namespace="orchestration", scope="read")
def list_capabilities(ctx: ToolContext) -> dict:
    """Report the tools available to the agent, grouped by namespace.

    Lets the agent introspect its own toolset (useful for planning at scale).
    """
    reg = ctx.registry
    if reg is None:
        raise ToolExecutionError("no registry on context")
    grouped: dict = {}
    for name in reg.names():
        ns = name.split(".", 1)[0]
        grouped.setdefault(ns, []).append(name)
    return {"namespaces": {k: v for k, v in grouped.items()}, "total": len(reg)}
