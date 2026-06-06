"""Provider-agnostic LLM layer.

The agent loop speaks only the vocabulary in :mod:`patchwork.llm.base`
(``Message``, ``ToolSpec``, ``AssistantTurn``). Concrete providers translate
to/from their own wire format. Swapping Anthropic for Gemini is a config flag,
never a code change in the agent.
"""
from patchwork.llm.base import (
    AssistantTurn,
    LLMClient,
    Message,
    Role,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)
from patchwork.llm.factory import build_llm

__all__ = [
    "AssistantTurn",
    "LLMClient",
    "Message",
    "Role",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "Usage",
    "build_llm",
]
