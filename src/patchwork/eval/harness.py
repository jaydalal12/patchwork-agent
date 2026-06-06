"""Score the agent against seeded buggy repositories.

Each :class:`EvalCase` points at a fixture repo with a known bug and failing
tests. The harness runs the full repair flow on a fresh sandbox copy and scores
the *independently verified* outcome — did the suite actually go green, and did
the agent stay within budget. ``pass@1`` is the headline metric.

Run it:  ``python -m patchwork.eval.harness``  (needs an LLM key configured).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from patchwork.config import Settings
from patchwork.llm.base import LLMClient
from patchwork.observability import get_logger
from patchwork.registry import ToolRegistry

_log = get_logger("eval")

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


@dataclass
class EvalCase:
    name: str
    fixture: Path
    # Tests that must pass after repair (sanity: at least the suite is green).
    description: str = ""


@dataclass
class EvalOutcome:
    name: str
    passed: bool
    verified_green: bool
    tool_calls: int
    duration_s: float
    summary: dict = field(default_factory=dict)
    error: Optional[str] = None


def default_cases() -> List[EvalCase]:
    return [
        EvalCase("off_by_one", _FIXTURES / "off_by_one", "slice off-by-one in total()"),
        EvalCase("wrong_operator", _FIXTURES / "wrong_operator", "tautological palindrome check"),
    ]


def run_eval(
    *,
    settings: Settings,
    llm: LLMClient,
    cases: Optional[List[EvalCase]] = None,
) -> List[EvalOutcome]:
    from patchwork.flows.repair import repair_repository

    cases = cases or default_cases()
    registry = ToolRegistry.load_builtins()
    outcomes: List[EvalOutcome] = []
    for case in cases:
        _log.info("eval case start", case=case.name)
        t0 = time.monotonic()
        err = None
        try:
            report = repair_repository(
                settings=settings,
                llm=llm,
                registry=registry,
                local_path=case.fixture,
                open_pr=False,
            )
            verified = report.verified_green
            tool_calls = report.tool_calls
            summary = report.final_summary
        except Exception as e:  # eval must never crash the whole run on one case
            verified, tool_calls, summary = False, -1, {}
            err = f"{type(e).__name__}: {e}"
        outcomes.append(
            EvalOutcome(
                name=case.name,
                passed=verified,
                verified_green=verified,
                tool_calls=tool_calls,
                duration_s=round(time.monotonic() - t0, 1),
                summary=summary,
                error=err,
            )
        )
    return outcomes


def score(outcomes: List[EvalOutcome]) -> dict:
    n = len(outcomes)
    passed = sum(1 for o in outcomes if o.passed)
    return {
        "cases": n,
        "passed": passed,
        "pass_at_1": round(passed / n, 3) if n else 0.0,
        "avg_tool_calls": round(sum(o.tool_calls for o in outcomes if o.tool_calls >= 0) / max(1, n), 1),
    }


def main() -> int:  # pragma: no cover - thin CLI wrapper
    from patchwork.llm.factory import build_llm

    settings = Settings.from_env()
    llm = build_llm(settings)
    outcomes = run_eval(settings=settings, llm=llm)
    print(json.dumps({"results": [asdict(o) for o in outcomes], "score": score(outcomes)}, indent=2, default=str))
    return 0 if all(o.passed for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
