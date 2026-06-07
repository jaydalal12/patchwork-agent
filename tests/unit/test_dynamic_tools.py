"""Dynamic tool loading: the meta-tools and the loop's per-turn advertisement."""
import pytest

from patchwork.agent.context import ConversationContext
from patchwork.agent.loop import run_agent
from patchwork.observability import Tracer
from patchwork.registry import ToolRegistry
from patchwork.tools.base import ToolContext
from tests.fakes import ScriptedLLM, final_turn, tool_turn


@pytest.fixture
def registry():
    return ToolRegistry.load_builtins()


def _ctx(registry):
    return ToolContext(settings=None, tracer=Tracer(), registry=registry)


def test_registry_splits_meta_from_domain(registry):
    meta = {s.name for s in registry.meta_specs()}
    domain = {s.name for s in registry.domain_specs()}
    assert "tools.load" in meta and "tools.search" in meta
    assert "ci.run_tests" in domain
    assert meta.isdisjoint(domain)
    assert len(domain) >= 50  # scale requirement holds on domain tools alone


def test_search_ranks_relevant_tools(registry):
    ctx = _ctx(registry)
    res = registry.execute("tools.search", {"query": "run the tests"}, ctx)
    assert not res.is_error
    assert "ci.run_tests" in res.content


def test_load_activates_by_name_and_namespace(registry):
    ctx = _ctx(registry)
    registry.execute("tools.load", {"names": ["code.read_file"]}, ctx)
    assert "code.read_file" in ctx.active_tools
    # loading a bare namespace activates all its tools
    registry.execute("tools.load", {"names": ["git"]}, ctx)
    assert "git.commit" in ctx.active_tools and "git.status" in ctx.active_tools


def test_load_reports_unknown(registry):
    ctx = _ctx(registry)
    res = registry.execute("tools.load", {"names": ["nope.zzz"]}, ctx)
    assert "nope.zzz" in res.content  # echoed back as unknown


def test_loop_advertises_only_meta_until_tools_loaded(registry):
    # Turn 1: load the ci namespace. Turn 2: stop. Assert advertisement grew.
    llm = ScriptedLLM(turns=[tool_turn(1, "tools.load", names=["ci"]), final_turn("done")])
    ctx = _ctx(registry)
    convo = ConversationContext(system="s", token_budget=100000)
    convo.add_user("go")
    run_agent(llm=llm, registry=registry, tool_ctx=ctx, conversation=convo,
              max_tool_calls=10, dynamic_tools=True)

    first, second = llm.seen_tools[0], llm.seen_tools[1]
    # First turn: only the meta-tools are visible.
    assert all(n.startswith("tools.") for n in first)
    # After loading ci, the suite tool is now advertised.
    assert "ci.run_tests" in second
    assert len(second) > len(first)
