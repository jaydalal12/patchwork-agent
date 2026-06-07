"""The flagship flow: fix a repo's failing tests and (optionally) open a PR.

This is the long-horizon task — a real run spans well past 20 tool calls
(orient → diagnose-with-subagent → fix → verify → review-with-subagent →
commit → PR). The flow owns setup/teardown and the *independent* verification
gate: regardless of what the agent claims, we re-run the suite ourselves and
mark the run unverified if it is still red.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from patchwork.agent.context import ConversationContext
from patchwork.agent.loop import AgentResult, run_agent
from patchwork.config import Settings
from patchwork.errors import BudgetExceededError
from patchwork.llm.base import LLMClient
from patchwork.observability import Tracer, get_logger
from patchwork.prompts import REPAIR_SYSTEM
from patchwork.registry import ToolRegistry
from patchwork.tools.base import ToolContext
from patchwork.tools.ci_tools import _parse_summary
from patchwork.tools.sandbox import RepoSandbox

_log = get_logger("flow.repair")


@dataclass
class RepairReport:
    verified_green: bool
    final_summary: Dict[str, int]
    agent_final_text: str
    tool_calls: int
    turns: int
    trace_summary: Dict[str, Any]
    branch: Optional[str] = None
    ledger: List[str] = field(default_factory=list)
    error: Optional[str] = None


def repair_repository(
    *,
    settings: Settings,
    llm: LLMClient,
    registry: ToolRegistry,
    local_path: Optional[Path] = None,
    clone_url: Optional[str] = None,
    task: Optional[str] = None,
    open_pr: bool = False,
    github_repo: Optional[str] = None,  # "owner/repo" for PR
) -> RepairReport:
    tracer = Tracer()
    if local_path:
        sandbox = RepoSandbox.from_local(local_path, settings.sandbox_root)
    elif clone_url:
        sandbox = RepoSandbox.from_clone(clone_url, settings.sandbox_root)
    else:
        raise ValueError("provide local_path or clone_url")

    github = None
    if settings.github_token and (open_pr or github_repo):
        from patchwork.tools.github_api import GitHubClient

        github = GitHubClient(settings.github_token)

    ctx = ToolContext(
        settings=settings,
        tracer=tracer,
        sandbox=sandbox,
        github=github,
        llm=llm,
        registry=registry,
    )

    instruction = task or (
        "Fix all failing tests in this repository with minimal correct changes, "
        "then summarize what was broken and what you changed."
    )
    if open_pr and github_repo:
        instruction += f"\nThen open a pull request on {github_repo} from your fix branch to the default branch."

    convo = ConversationContext(
        system=REPAIR_SYSTEM,
        token_budget=settings.context_token_budget,
        keep_recent=settings.context_keep_recent,
    )
    convo.add_user(instruction)

    error = None
    try:
        with tracer.span("flow.repair", "phase"):
            result: AgentResult = run_agent(
                llm=llm,
                registry=registry,
                tool_ctx=ctx,
                conversation=convo,
                max_tool_calls=settings.max_tool_calls,
            )
        final_text = result.final_text
        tool_calls, turns, ledger = result.tool_calls, result.turns, result.ledger
    except BudgetExceededError as e:
        error = str(e)
        final_text = "(stopped: budget exceeded)"
        tool_calls = settings.max_tool_calls
        turns = -1
        ledger = list(convo._ledger)

    # Independent verification gate — we do not trust the agent's self-report.
    verify = sandbox.run(["python", "-m", "pytest", "-q", "--tb=no"], timeout=300)
    summary = _parse_summary((verify.stdout + verify.stderr)[-4000:])
    verified_green = verify.returncode == 0
    branch = sandbox.run_git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()

    _log.info(
        "repair finished",
        verified_green=verified_green,
        tool_calls=tool_calls,
        summary=summary,
    )
    return RepairReport(
        verified_green=verified_green,
        final_summary=summary,
        agent_final_text=final_text,
        tool_calls=tool_calls,
        turns=turns,
        trace_summary=tracer.summary(),
        branch=branch,
        ledger=ledger,
        error=error,
    )
