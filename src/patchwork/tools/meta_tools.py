"""``tools.*`` — meta-tools for dynamic tool loading.

Sending all ~54 tool schemas on every request is the single biggest fixed cost
(~4.5k tokens) and blows small free-tier request/TPM caps. In dynamic mode the
loop advertises only these meta-tools plus whatever the model has *loaded*. The
model discovers tools with ``tools.search`` / ``tools.namespaces`` and activates
the handful it needs with ``tools.load`` — keeping each request small while the
full registry stays coherent behind it.

These tools mutate ``ctx.active_tools``; the control loop reads that set each
turn to decide which schemas to send.
"""
from __future__ import annotations

import re
from typing import List

from patchwork.errors import ToolExecutionError
from patchwork.tools.base import ToolContext, tool

META_NAMESPACE = "tools"


def _real_tools(ctx: ToolContext):
    """All registered tools except the meta-tools themselves."""
    reg = ctx.registry
    if reg is None:
        raise ToolExecutionError("no registry on context")
    return [reg.get(n) for n in reg.names() if not n.startswith(f"{META_NAMESPACE}.")]


@tool(namespace=META_NAMESPACE, scope="read")
def namespaces(ctx: ToolContext) -> dict:
    """List the available tool namespaces and how many tools each holds.

    Start here when tools are not yet loaded, then tools.search or tools.load.
    """
    counts: dict = {}
    for t in _real_tools(ctx):
        counts[t.namespace] = counts.get(t.namespace, 0) + 1
    return {"namespaces": counts, "hint": "use tools.search(query) or tools.load(names=[...])"}


@tool(
    namespace=META_NAMESPACE,
    scope="read",
    descriptions={"query": "keywords describing what you want to do, e.g. 'run tests' or 'open pull request'"},
)
def search(ctx: ToolContext, query: str) -> dict:
    """Find tools whose name or description match the query. Returns candidates
    to pass to tools.load — does not activate them."""
    terms = [t for t in re.split(r"\W+", query.lower()) if t]
    scored = []
    for t in _real_tools(ctx):
        hay = (t.name + " " + t.description).lower()
        score = sum(hay.count(term) for term in terms)
        if score:
            scored.append((score, t))
    scored.sort(key=lambda s: (-s[0], s[1].name))
    top = scored[:10]
    return {
        "matches": [
            {"name": t.name, "scope": t.scope, "description": t.description.split("\n")[0][:120]}
            for _, t in top
        ],
        "count": len(top),
    }


@tool(
    namespace=META_NAMESPACE,
    scope="read",
    descriptions={"names": "exact tool names to activate, e.g. ['ci.run_tests','code.read_file']"},
)
def load(ctx: ToolContext, names: List[str]) -> dict:
    """Activate tools by exact name so you can call them on the next turn.

    You may also pass a namespace (e.g. 'git') to load all of its tools.
    """
    reg = ctx.registry
    available = {t.name for t in _real_tools(ctx)}
    namespaces_avail = {t.namespace for t in _real_tools(ctx)}
    loaded, unknown = [], []
    for n in names:
        if n in available:
            ctx.active_tools.add(n)
            loaded.append(n)
        elif n in namespaces_avail:
            ns_tools = [t.name for t in _real_tools(ctx) if t.namespace == n]
            ctx.active_tools.update(ns_tools)
            loaded.extend(ns_tools)
        else:
            unknown.append(n)
    return {
        "loaded": sorted(set(loaded)),
        "unknown": unknown,
        "active_total": len(ctx.active_tools),
    }
