"""The tool registry: collection, scoped views, and validated dispatch.

Selection is the model's job — we hand it every tool's schema and it picks.
Our job is to make dispatch *coherent*: O(1) lookup, schema-validated inputs,
typed failures, and a span per call. ``scoped()`` produces a restricted
registry for subagents (e.g. read-only), enforced at execution time, not just
advertised.
"""
from __future__ import annotations

import importlib
import json
from typing import Any, Dict, Iterable, List, Optional

from patchwork.errors import ToolExecutionError, ToolInputError, ToolNotFoundError
from patchwork.llm.base import ToolResult, ToolSpec
from patchwork.observability import get_logger
from patchwork.tools.base import _REGISTERED, Scope, Tool, ToolContext

_log = get_logger("registry")

# Tool modules whose import side-effect registers their tools.
_BUILTIN_MODULES = [
    "patchwork.tools.git_tools",
    "patchwork.tools.github_tools",
    "patchwork.tools.ci_tools",
    "patchwork.tools.code_tools",
    "patchwork.tools.orchestration_tools",
]


class ToolRegistry:
    def __init__(self, tools: Optional[Iterable[Tool]] = None):
        self._tools: Dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    # -- construction -----------------------------------------------------
    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    @classmethod
    def load_builtins(cls) -> "ToolRegistry":
        # Importing a tool module runs its @tool decorators exactly once (Python
        # caches modules), appending to _REGISTERED. We must NOT clear the list:
        # a second call re-imports the *cached* modules, which would not re-run
        # the decorators, leaving us with nothing. De-dup defensively instead.
        for mod in _BUILTIN_MODULES:
            importlib.import_module(mod)
        seen: Dict[str, Tool] = {}
        for t in _REGISTERED:
            seen[t.name] = t  # last definition wins; names are unique anyway
        reg = cls(seen.values())
        _log.info(
            "registry loaded",
            tools=len(reg._tools),
            namespaces=sorted(reg.namespaces()),
        )
        return reg

    # -- views ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def namespaces(self) -> List[str]:
        return sorted({t.namespace for t in self._tools.values()})

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"no such tool: {name}", tool=name) from None

    def specs(self) -> List[ToolSpec]:
        """What we advertise to the model."""
        return [
            ToolSpec(name=t.name, description=t.description, input_schema=t.input_schema)
            for t in self._tools.values()
        ]

    def scoped(
        self,
        *,
        namespaces: Optional[Iterable[str]] = None,
        names: Optional[Iterable[str]] = None,
        scope: Optional[Scope] = None,
    ) -> "ToolRegistry":
        """A restricted registry for a subagent.

        Filters compose (intersection). A subagent given ``scope="read"`` cannot
        even *see* — let alone call — a write tool.
        """
        ns = set(namespaces) if namespaces else None
        nm = set(names) if names else None
        selected = [
            t
            for t in self._tools.values()
            if (ns is None or t.namespace in ns)
            and (nm is None or t.name in nm)
            and (scope is None or t.scope == scope or (scope == "write"))
        ]
        # scope=="read" keeps only read tools; scope=="write" keeps all (write+read).
        if scope == "read":
            selected = [t for t in selected if t.scope == "read"]
        return ToolRegistry(selected)

    # -- validation + dispatch -------------------------------------------
    def _validate(self, tool: Tool, args: Dict[str, Any]) -> None:
        schema = tool.input_schema
        required = schema.get("required", [])
        missing = [r for r in required if r not in args]
        if missing:
            raise ToolInputError(
                f"{tool.name}: missing required args {missing}", tool=tool.name
            )
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}))
            extra = [k for k in args if k not in allowed]
            if extra:
                raise ToolInputError(
                    f"{tool.name}: unexpected args {extra} (allowed: {sorted(allowed)})",
                    tool=tool.name,
                )

    def execute(self, name: str, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Validate + run a tool, always returning a ToolResult (never raising
        for *tool-level* failures — those come back as ``is_error=True`` so the
        model can read the error and recover). Programmer errors still raise.
        """
        tool = self.get(name)  # raises ToolNotFoundError -> caller decides
        with ctx.tracer.span(name, "tool", scope=tool.scope) as sp:
            try:
                self._validate(tool, args)  # ToolInputError -> error result, not a crash
                result = tool(ctx, **args)
                payload = result if isinstance(result, str) else json.dumps(result, default=str)
                return ToolResult(tool_call_id="", name=name, content=payload, is_error=False)
            except ToolInputError as e:
                sp.ok = False
                _log.warning("tool input invalid", tool=name, error=str(e))
                return ToolResult(tool_call_id="", name=name, content=f"ERROR: {e}", is_error=True)
            except ToolExecutionError as e:
                sp.ok = False
                _log.warning("tool failed", tool=name, error=str(e))
                return ToolResult(tool_call_id="", name=name, content=f"ERROR: {e}", is_error=True)
            except Exception as e:  # unexpected — wrap, mark error, let model see it
                sp.ok = False
                _log.warning("tool crashed", tool=name, error=repr(e))
                return ToolResult(
                    tool_call_id="", name=name, content=f"ERROR: {type(e).__name__}: {e}", is_error=True
                )
