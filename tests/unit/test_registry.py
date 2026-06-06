import pytest

from patchwork.errors import ToolNotFoundError
from patchwork.observability import Tracer
from patchwork.registry import ToolRegistry
from patchwork.tools.base import ToolContext


@pytest.fixture
def registry():
    return ToolRegistry.load_builtins()


def test_meets_scale_requirement(registry):
    assert len(registry) >= 50
    assert len(registry.namespaces()) >= 4


def test_specs_match_tools(registry):
    specs = registry.specs()
    assert len(specs) == len(registry)
    for s in specs:
        assert "." in s.name
        assert s.description  # every tool advertises a description


def test_read_scope_excludes_write_tools(registry):
    read_only = registry.scoped(scope="read")
    names = set(read_only.names())
    assert "git.commit" not in names  # write
    assert "git.status" in names  # read
    assert "github.open_pull_request" not in names


def test_namespace_scoping(registry):
    only_ci = registry.scoped(namespaces=["ci"])
    assert all(n.startswith("ci.") for n in only_ci.names())
    assert len(only_ci) > 0


def test_unknown_tool_raises(registry):
    ctx = ToolContext(settings=None, tracer=Tracer())
    with pytest.raises(ToolNotFoundError):
        registry.execute("git.nope", {}, ctx)


def test_invalid_input_returns_error_result_not_crash(registry):
    ctx = ToolContext(settings=None, tracer=Tracer())
    # git.create_branch requires 'name'; omit it.
    res = registry.execute("git.create_branch", {}, ctx)
    assert res.is_error
    assert "missing required" in res.content
