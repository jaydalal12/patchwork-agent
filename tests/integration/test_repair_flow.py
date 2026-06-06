"""End-to-end: a scripted LLM drives the REAL loop, registry, sandbox, and
pytest to fix the off_by_one fixture. No API key, no network — but every layer
below the model is exercised for real, including the independent verify gate.
"""
from pathlib import Path

import pytest

from patchwork.config import Settings
from patchwork.flows.repair import repair_repository
from patchwork.registry import ToolRegistry
from tests.fakes import ScriptedLLM, final_turn, tool_turn

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.mark.integration
def test_agent_fixes_off_by_one(tmp_path):
    # A plausible trajectory: run tests -> read source -> patch -> re-run -> report.
    llm = ScriptedLLM(
        turns=[
            tool_turn(1, "ci.run_tests"),
            tool_turn(2, "code.read_file", path="calc.py"),
            tool_turn(3, "code.replace_in_file", path="calc.py",
                      old="return sum(items[1:])", new="return sum(items)"),
            tool_turn(4, "ci.run_tests"),
            final_turn("Fixed an off-by-one slice in total(); suite is green."),
        ]
    )
    settings = Settings(sandbox_root=tmp_path, max_tool_calls=20)
    registry = ToolRegistry.load_builtins()

    report = repair_repository(
        settings=settings,
        llm=llm,
        registry=registry,
        local_path=FIXTURES / "off_by_one",
    )

    assert report.verified_green is True
    assert report.final_summary.get("failed", 0) == 0
    assert report.tool_calls == 4
    assert report.trace_summary["tool_calls"] == 4


@pytest.mark.integration
def test_agent_fixes_multi_bug(tmp_path):
    # Two independent fixes in one session — exercises a longer trajectory.
    llm = ScriptedLLM(
        turns=[
            tool_turn(1, "ci.run_tests"),
            tool_turn(2, "code.read_file", path="bank.py"),
            # Fix withdraw first (the unique "+ amount"), then deposit (the
            # now-first "- amount"); order matters because replace_in_file hits
            # the first occurrence.
            tool_turn(3, "code.replace_in_file", path="bank.py",
                      old="return balance + amount", new="return balance - amount"),
            tool_turn(4, "code.replace_in_file", path="bank.py",
                      old="return balance - amount", new="return balance + amount"),
            tool_turn(5, "ci.run_tests"),
            final_turn("Fixed deposit/withdraw sign errors; suite green."),
        ]
    )
    settings = Settings(sandbox_root=tmp_path, max_tool_calls=20)
    report = repair_repository(
        settings=settings, llm=llm, registry=ToolRegistry.load_builtins(),
        local_path=FIXTURES / "multi_bug",
    )
    assert report.verified_green is True
    assert report.final_summary.get("passed", 0) >= 3


@pytest.mark.integration
def test_verify_gate_catches_false_success(tmp_path):
    # The model *claims* done without fixing anything. The independent gate
    # must report the suite is still red.
    llm = ScriptedLLM(turns=[final_turn("All good! (it is not)")])
    settings = Settings(sandbox_root=tmp_path, max_tool_calls=20)
    registry = ToolRegistry.load_builtins()

    report = repair_repository(
        settings=settings, llm=llm, registry=registry, local_path=FIXTURES / "off_by_one"
    )
    assert report.verified_green is False
    assert report.final_summary.get("failed", 0) >= 1
