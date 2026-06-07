"""Groq (OpenAI-style) translation + parsing, as pure functions (no SDK/network)."""
from types import SimpleNamespace as NS

from patchwork.llm import groq_client as q
from patchwork.llm.base import Message, ToolCall, ToolResult, ToolSpec

SPECS = [ToolSpec(name="git.status", description="status", input_schema={"type": "object", "properties": {}})]
CONVO = [
    Message(role="user", text="fix it"),
    Message(role="assistant", text="", tool_calls=[ToolCall(id="c1", name="git.status", arguments={"x": 1})]),
    Message(role="tool", tool_result=ToolResult(tool_call_id="c1", name="git.status", content="clean")),
]


def test_messages_put_system_first_and_serialize_tool_calls():
    out = q.to_messages("SYS", CONVO)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == {"role": "user", "content": "fix it"}
    tc = out[2]["tool_calls"][0]
    assert tc["function"]["name"] == "git.status"
    assert tc["function"]["arguments"] == '{"x": 1}'  # arguments are a JSON string
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "name": "git.status", "content": "clean"}


def test_tools_use_openai_function_envelope():
    t = q.to_tools(SPECS)[0]
    assert t["type"] == "function"
    assert t["function"]["name"] == "git.status"


def test_parse_decodes_tool_call_arguments_and_usage():
    resp = NS(
        choices=[NS(
            message=NS(
                content=None,
                tool_calls=[NS(id="t1", function=NS(name="ci.run_tests", arguments='{"target": "x"}'))],
            ),
            finish_reason="tool_calls",
        )],
        usage=NS(prompt_tokens=12, completion_tokens=4),
    )
    turn = q.parse_response(resp)
    assert turn.tool_calls[0].name == "ci.run_tests"
    assert turn.tool_calls[0].arguments == {"target": "x"}
    assert turn.usage.input_tokens == 12
    assert turn.stop_reason == "tool_calls"


def test_parse_tolerates_malformed_arguments():
    resp = NS(
        choices=[NS(
            message=NS(content="hi", tool_calls=[NS(id="t1", function=NS(name="x.y", arguments="not json"))]),
            finish_reason="stop",
        )],
        usage=None,
    )
    turn = q.parse_response(resp)
    assert turn.tool_calls[0].arguments == {}  # falls back, does not crash
    assert turn.text == "hi"
