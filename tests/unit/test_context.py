from patchwork.agent.context import ConversationContext
from patchwork.llm.base import ToolResult


def _ctx():
    c = ConversationContext(system="sys", token_budget=200, keep_recent=2)
    c.add_user("fix the failing tests please")  # pinned task
    return c


def test_compaction_reduces_tokens_and_pins_task():
    c = _ctx()
    big = "X" * 4000
    for i in range(8):
        c.add_tool_result(ToolResult(tool_call_id=str(i), name="ci.run_tests", content=big))
    before = c.estimated_tokens()
    changed = c.compact_if_needed()
    after = c.estimated_tokens()
    assert changed is True
    assert after < before
    # The pinned task survives verbatim.
    assert c.messages[0].text == "fix the failing tests please"


def test_recent_messages_kept_verbatim():
    c = _ctx()
    big = "Y" * 4000
    for i in range(8):
        c.add_tool_result(ToolResult(tool_call_id=str(i), name="t", content=big))
    c.compact_if_needed()
    # last keep_recent (2) tool results are untouched
    assert "compacted" not in (c.messages[-1].tool_result.content)
    assert "compacted" not in (c.messages[-2].tool_result.content)


def test_ledger_survives_and_renders():
    c = _ctx()
    c.note("ci.run_tests() -> ok")
    assert "PROGRESS LEDGER" in c.system_with_ledger()
    assert "ci.run_tests() -> ok" in c.system_with_ledger()


def test_no_compaction_under_budget():
    c = ConversationContext(system="s", token_budget=100000)
    c.add_user("hello")
    assert c.compact_if_needed() is False
