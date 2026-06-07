"""``ci.*`` tools — run and interpret the test suite.

The keystone is ``ci.run_tests``: it runs pytest, parses the machine-readable
summary, and stashes the structured failure list in ``ctx.scratch`` so other
tools (and the agent) can compose on it without re-running. ``ci.parse_failures``
is the composition surface — it reads that structured output.
"""
from __future__ import annotations

import json
import re
from typing import List

from patchwork.errors import ToolExecutionError
from patchwork.tools.base import ToolContext, tool

_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")
_FAIL_LINE_RE = re.compile(r"^(FAILED|ERROR) (\S+?)(?:::(\S+))?(?: - (.*))?$", re.MULTILINE)


def _sb(ctx: ToolContext):
    if ctx.sandbox is None:
        raise ToolExecutionError("no sandbox on context")
    return ctx.sandbox


def _parse_summary(text: str) -> dict:
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for n, kind in _SUMMARY_RE.findall(text):
        key = "error" if kind.startswith("error") else kind
        counts[key] = counts.get(key, 0) + int(n)
    return counts


def _parse_failures(text: str) -> List[dict]:
    out = []
    for kind, path, test, msg in _FAIL_LINE_RE.findall(text):
        out.append(
            {
                "kind": kind.lower(),
                "file": path,
                "test": test or None,
                "message": (msg or "").strip()[:300],
            }
        )
    return out


@tool(namespace="ci", scope="read", descriptions={"target": "optional path/node id to limit the run"})
def run_tests(ctx: ToolContext, target: str = "") -> dict:
    """Run the pytest suite (optionally a subset) and return a structured summary.

    Stores the parsed failure list in scratch under 'last_failures' for
    composition by other tools.
    """
    cmd = ["python", "-m", "pytest", "-q", "--tb=short", "-rfE"]
    if target:
        cmd.append(target)
    r = _sb(ctx).run(cmd, timeout=300)
    tail = (r.stdout + "\n" + r.stderr)[-8000:]
    counts = _parse_summary(tail)
    failures = _parse_failures(tail)
    ctx.scratch["last_failures"] = failures
    ctx.scratch["last_test_output"] = tail
    return {
        "returncode": r.returncode,
        "green": r.returncode == 0,
        "counts": counts,
        "failures": failures,
        "output_tail": tail[-1200:],
    }


@tool(namespace="ci", scope="read", descriptions={"node_id": "pytest node id e.g. tests/test_x.py::test_y"})
def run_single_test(ctx: ToolContext, node_id: str) -> dict:
    """Run one test by its pytest node id; useful to confirm a targeted fix."""
    return run_tests(ctx, target=node_id)


@tool(namespace="ci", scope="read")
def list_tests(ctx: ToolContext) -> dict:
    """Collect (without running) all test node ids pytest can discover."""
    r = _sb(ctx).run(["python", "-m", "pytest", "--collect-only", "-q"], timeout=120)
    ids = [l for l in r.stdout.splitlines() if "::" in l]
    return {"count": len(ids), "tests": ids[:200]}


@tool(namespace="ci", scope="read")
def parse_failures(ctx: ToolContext) -> dict:
    """Return the structured failure list from the most recent ci.run_tests.

    This is the composition point: consumes the structured output another tool
    produced rather than re-running the suite.
    """
    failures = ctx.scratch.get("last_failures")
    if failures is None:
        raise ToolExecutionError("no test run recorded yet; call ci.run_tests first")
    return {"count": len(failures), "failures": failures}


@tool(namespace="ci", scope="read", descriptions={"node_id": "failing test node id"})
def failure_detail(ctx: ToolContext, node_id: str) -> str:
    """Re-run a single failing test with a long traceback for root-cause analysis."""
    r = _sb(ctx).run(
        ["python", "-m", "pytest", node_id, "-q", "--tb=long", "-rA"], timeout=180
    )
    return (r.stdout + "\n" + r.stderr)[-2500:]


@tool(namespace="ci", scope="read", descriptions={"path": "python file to syntax-check"})
def syntax_check(ctx: ToolContext, path: str) -> dict:
    """Byte-compile a Python file to catch syntax errors before running tests."""
    r = _sb(ctx).run(["python", "-m", "py_compile", path], timeout=30)
    return {"path": path, "ok": r.ok, "error": r.stderr.strip()[:500] if not r.ok else None}


@tool(namespace="ci", scope="read")
def typecheck(ctx: ToolContext) -> dict:
    """Run mypy if available; reports gracefully if the tool is not installed."""
    r = _sb(ctx).run(["python", "-m", "mypy", ".", "--no-error-summary"], timeout=180)
    if "No module named" in r.stderr:
        return {"available": False, "note": "mypy not installed"}
    return {"available": True, "ok": r.ok, "output": (r.stdout or r.stderr)[:3000]}


@tool(namespace="ci", scope="read")
def coverage(ctx: ToolContext) -> dict:
    """Run the suite under coverage if the plugin is available; report totals."""
    r = _sb(ctx).run(
        ["python", "-m", "pytest", "-q", "--cov=.", "--cov-report=term-missing"], timeout=300
    )
    if "unrecognized arguments" in r.stderr or "No module named" in r.stderr:
        return {"available": False, "note": "pytest-cov not installed"}
    m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", r.stdout)
    return {"available": True, "total_pct": int(m.group(1)) if m else None, "output_tail": r.stdout[-1500:]}


@tool(namespace="ci", scope="read")
def show_last_output(ctx: ToolContext) -> str:
    """Return the raw output tail of the most recent ci.run_tests (composition helper)."""
    out = ctx.scratch.get("last_test_output")
    if out is None:
        raise ToolExecutionError("no test run recorded yet; call ci.run_tests first")
    return out[-4000:]


@tool(namespace="ci", scope="read")
def detect_test_command(ctx: ToolContext) -> dict:
    """Inspect the repo to report how its tests are configured (pytest/unittest)."""
    sb = _sb(ctx)
    signals = {
        "pyproject_pytest": sb.exists("pyproject.toml") and "pytest" in (sb.read("pyproject.toml") if sb.exists("pyproject.toml") else ""),
        "has_tests_dir": sb.exists("tests"),
        "has_setup_cfg": sb.exists("setup.cfg"),
        "has_tox": sb.exists("tox.ini"),
    }
    return {"recommended": "python -m pytest", "signals": signals}
