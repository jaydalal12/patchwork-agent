import os

import pytest

from patchwork.config import Settings
from patchwork.errors import ConfigError
from patchwork.llm.gemini_client import _clean_schema
from patchwork.tools.base import _build_schema


def sample(ctx, name: str, count: int = 3, flags: list = []):
    """doc"""
    return None


def test_schema_inference_types_and_required():
    schema = _build_schema(sample, {"name": "the name"})
    props = schema["properties"]
    assert props["name"] == {"type": "string", "description": "the name"}
    assert props["count"]["type"] == "integer"
    assert props["flags"]["type"] == "array"
    assert schema["required"] == ["name"]  # only the no-default param
    assert schema["additionalProperties"] is False
    assert "ctx" not in props  # ctx is never advertised


def test_gemini_schema_strips_unsupported_keys():
    cleaned = _clean_schema(
        {"type": "object", "additionalProperties": False,
         "properties": {"x": {"type": "string", "title": "X"}}}
    )
    assert "additionalProperties" not in cleaned
    assert "title" not in cleaned["properties"]["x"]


def test_provider_prefers_anthropic_then_gemini():
    s = Settings(anthropic_api_key="a", gemini_api_key="g")
    assert s.resolved_provider() == "anthropic"
    s2 = Settings(gemini_api_key="g")
    assert s2.resolved_provider() == "gemini"


def test_no_keys_raises_config_error():
    s = Settings()
    with pytest.raises(ConfigError):
        s.resolved_provider()
