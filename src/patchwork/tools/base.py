"""Tool definition, execution context, and the ``@tool`` decorator.

A tool is a plain Python function plus metadata. The decorator derives a JSON
Schema from the function's type hints so we never hand-maintain schemas, and
registers the tool into a module-level table that :class:`ToolRegistry`
collects. Dispatch is a dict lookup keyed by ``namespace.action`` — there is no
giant conditional anywhere, which is what keeps the registry coherent at fifty
tools.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, get_args, get_origin, get_type_hints

Scope = Literal["read", "write"]

# Tools register here at import time; ToolRegistry.load_builtins() collects them.
_REGISTERED: List["Tool"] = []

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _json_type(annotation: Any) -> Dict[str, Any]:
    origin = get_origin(annotation)
    if origin is None:
        if annotation in _PY_TO_JSON:
            return {"type": _PY_TO_JSON[annotation]}
        if annotation in (dict, Dict):
            return {"type": "object"}
        if annotation in (list, List):
            return {"type": "array"}
        return {"type": "string"}  # fall back to string for unknown/complex
    if origin in (list, List):
        args = get_args(annotation)
        item = _json_type(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}
    if origin in (dict, Dict):
        return {"type": "object"}
    # Optional[X] / Union[X, None]
    args = [a for a in get_args(annotation) if a is not type(None)]
    if args:
        return _json_type(args[0])
    return {"type": "string"}


def _build_schema(fn: Callable, descriptions: Dict[str, str]) -> Dict[str, Any]:
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}
    props: Dict[str, Any] = {}
    required: List[str] = []
    for name, param in sig.parameters.items():
        if name == "ctx":
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        ann = hints.get(name, str)
        prop = _json_type(ann)
        if name in descriptions:
            prop["description"] = descriptions[name]
        props[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: Dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return schema


@dataclass
class Tool:
    name: str  # "namespace.action"
    namespace: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[..., Any]
    scope: Scope = "read"

    def __call__(self, ctx: "ToolContext", **kwargs: Any) -> Any:
        return self.handler(ctx, **kwargs)


def tool(
    *,
    namespace: str,
    scope: Scope = "read",
    name: Optional[str] = None,
    descriptions: Optional[Dict[str, str]] = None,
) -> Callable[[Callable], Callable]:
    """Register ``fn`` as a tool. The action name defaults to the function name.

    ``scope`` ("read"/"write") lets the registry hand a subagent a restricted
    slice — e.g. a read-only analyzer that physically cannot mutate the repo.
    """

    def deco(fn: Callable) -> Callable:
        action = name or fn.__name__
        full = f"{namespace}.{action}"
        doc = (fn.__doc__ or "").strip()
        if not doc:
            raise ValueError(f"tool {full} must have a docstring (it becomes the model-facing description)")
        schema = _build_schema(fn, descriptions or {})
        _REGISTERED.append(
            Tool(
                name=full,
                namespace=namespace,
                description=doc,
                input_schema=schema,
                handler=fn,
                scope=scope,
            )
        )
        fn.__patchwork_tool__ = full  # type: ignore[attr-defined]
        return fn

    return deco


@dataclass
class ToolContext:
    """Shared state every tool receives as its first argument.

    Threading one context object (rather than globals) keeps tools pure-ish and
    trivially testable: construct a context with fakes and call the handler.
    """

    settings: Any  # patchwork.config.Settings
    tracer: Any  # patchwork.observability.Tracer
    sandbox: Any = None  # patchwork.tools.sandbox.RepoSandbox
    github: Any = None  # patchwork.tools.github_api.GitHubClient
    llm: Any = None  # patchwork.llm.base.LLMClient (for subagents)
    registry: Any = None  # patchwork.registry.ToolRegistry (for subagents)
    scratch: Dict[str, Any] = field(default_factory=dict)
