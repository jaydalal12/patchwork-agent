"""Command-line entry point.

    patchwork tools                 # list the registry, grouped by namespace
    patchwork doctor                # check config / keys / git availability
    patchwork run --repo PATH       # fix failing tests in a local repo
    patchwork run --clone URL [--open-pr --github-repo owner/repo]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from patchwork.config import Settings
from patchwork.errors import PatchworkError
from patchwork.observability import configure_logging, get_logger
from patchwork.registry import ToolRegistry

_log = get_logger("cli")


def _cmd_tools(args, settings: Settings) -> int:
    reg = ToolRegistry.load_builtins()
    grouped: dict = {}
    for name in reg.names():
        grouped.setdefault(name.split(".", 1)[0], []).append(name)
    print(f"{len(reg)} tools across {len(grouped)} namespaces:\n")
    for ns in sorted(grouped):
        print(f"  {ns} ({len(grouped[ns])}):")
        for n in grouped[ns]:
            print(f"    - {n}")
    return 0


def _cmd_doctor(args, settings: Settings) -> int:
    import shutil

    checks = {
        "git on PATH": shutil.which("git") is not None,
        "ANTHROPIC_API_KEY set": bool(settings.anthropic_api_key),
        "GEMINI_API_KEY set": bool(settings.gemini_api_key),
        "GITHUB_TOKEN set": bool(settings.github_token),
    }
    try:
        provider = settings.resolved_provider()
    except PatchworkError as e:
        provider = f"UNRESOLVED ({e})"
    reg = ToolRegistry.load_builtins()
    print("patchwork doctor\n")
    for k, v in checks.items():
        print(f"  [{'ok' if v else '--'}] {k}")
    print(f"\n  provider: {provider}")
    print(f"  tools: {len(reg)} across {len(reg.namespaces())} namespaces {reg.namespaces()}")
    return 0


def _cmd_run(args, settings: Settings) -> int:
    from patchwork.flows.repair import repair_repository
    from patchwork.llm.factory import build_llm

    llm = build_llm(settings)
    registry = ToolRegistry.load_builtins()
    report = repair_repository(
        settings=settings,
        llm=llm,
        registry=registry,
        local_path=Path(args.repo) if args.repo else None,
        clone_url=args.clone,
        task=args.task,
        open_pr=args.open_pr,
        github_repo=args.github_repo,
    )
    out = {
        "verified_green": report.verified_green,
        "final_summary": report.final_summary,
        "tool_calls": report.tool_calls,
        "turns": report.turns,
        "branch": report.branch,
        "trace": report.trace_summary,
        "error": report.error,
        "final_report": report.agent_final_text,
    }
    print(json.dumps(out, indent=2))
    return 0 if report.verified_green else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="patchwork", description="Autonomous test-fixing agent.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tools", help="list the tool registry")
    sub.add_parser("doctor", help="check configuration and environment")

    run = sub.add_parser("run", help="fix failing tests in a repository")
    src = run.add_mutually_exclusive_group(required=True)
    src.add_argument("--repo", help="path to a local repository")
    src.add_argument("--clone", help="git URL to clone")
    run.add_argument("--task", help="override the default instruction")
    run.add_argument("--open-pr", action="store_true", help="open a PR when green")
    run.add_argument("--github-repo", help="owner/repo for the PR")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.log_json)
    try:
        if args.cmd == "tools":
            return _cmd_tools(args, settings)
        if args.cmd == "doctor":
            return _cmd_doctor(args, settings)
        if args.cmd == "run":
            return _cmd_run(args, settings)
    except PatchworkError as e:
        _log.error("patchwork error", error=str(e), type=type(e).__name__)
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
