"""Structured logging + lightweight tracing.

Two needs, one module:

* **Logs** — every meaningful event goes through :func:`get_logger`. In
  production (``PATCHWORK_LOG_JSON=true``) each record is a single JSON line,
  ready for any log pipeline; locally it's human-readable.
* **Traces** — :class:`Tracer` records a flat span list per run (tool calls,
  LLM turns, subagent runs) with durations, so a finished run can be replayed
  and timed without a vendor SDK. ``trace.summary()`` feeds the eval harness.

Kept dependency-free on purpose: observability you can read is observability
you'll actually keep.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Structured extras attached via logger.info(msg, extra={"extra_fields": {...}})
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_mode: bool = False) -> None:
    global _CONFIGURED
    handler = logging.StreamHandler(sys.stderr)
    if json_mode:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s %(name)s | %(message)s", "%H:%M:%S")
        )
    root = logging.getLogger("patchwork")
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.LoggerAdapter:
    if not _CONFIGURED:
        configure_logging()
    base = logging.getLogger(f"patchwork.{name}")

    class _Adapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            # Allow logger.info("x", field=1) -> structured extra.
            fields = {k: kwargs.pop(k) for k in list(kwargs) if k not in ("exc_info", "stack_info", "stacklevel")}
            if fields:
                kwargs.setdefault("extra", {})["extra_fields"] = fields
            return msg, kwargs

    return _Adapter(base, {})


@dataclass
class Span:
    name: str
    kind: str  # "tool" | "llm" | "subagent" | "phase"
    start: float
    end: Optional[float] = None
    ok: bool = True
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> Optional[float]:
        if self.end is None:
            return None
        return round((self.end - self.start) * 1000, 1)


@dataclass
class Tracer:
    """Flat, in-memory span recorder for a single agent run."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    spans: List[Span] = field(default_factory=list)

    @contextmanager
    def span(self, name: str, kind: str, **attrs: Any) -> Iterator[Span]:
        s = Span(name=name, kind=kind, start=time.monotonic(), attrs=dict(attrs))
        self.spans.append(s)
        try:
            yield s
        except BaseException:
            s.ok = False
            raise
        finally:
            s.end = time.monotonic()

    def summary(self) -> Dict[str, Any]:
        by_kind: Dict[str, int] = {}
        for s in self.spans:
            by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
        return {
            "run_id": self.run_id,
            "total_spans": len(self.spans),
            "counts_by_kind": by_kind,
            "tool_calls": by_kind.get("tool", 0),
            "failed_spans": sum(1 for s in self.spans if not s.ok),
        }
