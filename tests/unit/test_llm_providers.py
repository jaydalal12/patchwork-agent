"""Provider translation + response parsing, tested as pure functions.

No SDK, no network: we feed the parsers fake response objects shaped like the
real SDKs return ("cassettes"). This covers the code most likely to break when
swapping providers — message/tool translation and tool-call extraction.
"""
from types import SimpleNamespace as NS

from patchwork.llm import anthropic_client as ac
from patchwork.llm import gemini_client as gc
from patchwork.llm.base import Message, ToolCall, ToolResult, ToolSpec

SPECS = [ToolSpec(name="git.status", description="status", input_schema={"type": "object", "properties": {}})]

CONVO = [
    Message(role="user", text="fix it"),
    Message(role="assistant", text="", tool_calls=[ToolCall(id="c1", name="git.status", arguments={})]),
    Message(role="tool", tool_result=ToolResult(tool_call_id="c1", name="git.status", content="clean")),
]


# --- Anthropic ---------------------------------------------------------------
def test_anthropic_message_translation_roundtrips_roles():
    out = ac.to_api_messages(CONVO)
    assert out[0] == {"role": "user", "content": "fix it"}
    assert out[1]["role"] == "assistant"
    assert out[1]["content"][0]["type"] == "tool_use"
    # tool results map to a user message with a tool_result block
    assert out[2]["content"][0]["type"] == "tool_result"
    assert out[2]["content"][0]["tool_use_id"] == "c1"


def test_anthropic_tool_names_sanitized_no_dots():
    # Anthropic rejects '.' in tool names; we swap to '__' and back.
    name = ac.to_api_tools(SPECS)[0]["name"]
    assert name == "git__status"
    assert "." not in name
    assert ac._desan(ac._san("ci.run_tests")) == "ci.run_tests"


def test_anthropic_assistant_tool_calls_sanitized_on_resend():
    out = ac.to_api_messages(CONVO)
    assert out[1]["content"][0]["name"] == "git__status"  # no dot on the wire


def test_anthropic_parse_desanitizes_tool_call_names():
    resp = NS(
        content=[
            NS(type="text", text="working on it"),
            NS(type="tool_use", id="t1", name="ci__run_tests", input={"target": "x"}),
        ],
        usage=NS(input_tokens=11, output_tokens=7),
        stop_reason="tool_use",
    )
    turn = ac.parse_response(resp)
    assert turn.text == "working on it"
    assert turn.tool_calls[0].name == "ci.run_tests"  # desanitized back to dotted
    assert turn.tool_calls[0].arguments == {"target": "x"}
    assert turn.usage.input_tokens == 11


# --- Gemini ------------------------------------------------------------------
def test_gemini_content_translation_uses_model_role_and_function_response():
    out = gc.to_contents(CONVO)
    assert out[1]["role"] == "model"
    assert out[1]["parts"][0]["function_call"]["name"] == "git.status"
    # tool result becomes a function_response keyed by tool name
    assert out[2]["parts"][0]["function_response"]["name"] == "git.status"
    assert out[2]["parts"][0]["function_response"]["response"] == {"result": "clean"}


def test_gemini_tool_declarations_strip_unsupported_keys():
    spec = ToolSpec(name="x", description="d",
                    input_schema={"type": "object", "additionalProperties": False, "properties": {}})
    decls = gc.to_tools([spec])[0]["function_declarations"][0]
    assert "additionalProperties" not in decls["parameters"]


def test_gemini_parse_extracts_function_call():
    resp = NS(
        candidates=[NS(content=NS(parts=[
            NS(function_call=NS(name="code.read_file", args={"path": "a.py"})),
            NS(text="and some prose", function_call=None),
        ]), finish_reason="STOP")],
        usage_metadata=NS(prompt_token_count=20, candidates_token_count=9),
    )
    turn = gc.parse_response(resp)
    assert turn.tool_calls[0].name == "code.read_file"
    assert turn.tool_calls[0].arguments == {"path": "a.py"}
    assert "prose" in turn.text
    assert turn.usage.output_tokens == 9
