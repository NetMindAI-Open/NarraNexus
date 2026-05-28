"""
@file_name: test_structured_fallback_ladder.py
@description: Unit tests for the 3-level structured-output fallback ladder
   in OpenAIAgentsSDK._fallback_chat_completion.

The ladder:
   1. response_format = {"type": "json_schema", strict=True, schema=...}
   2. response_format = {"type": "json_object"}
   3. No response_format (prompt-engineering only — original behavior)

These tests stub out the OpenAI client so they run offline and deterministically.
Each test asserts:
  - levels are tried in the right priority order
  - "unsupported response_format" errors drop the level from the capability cache
  - non-response_format errors propagate (don't downgrade the level silently)
  - the cache short-circuits subsequent calls for the same (base_url, model)

For a live integration smoke test that hits real NetMind endpoints, see
``tests/agent_framework/_manual/smoke_structured_fallback.py`` (gated by
``NETMIND_API_KEY`` env var; not run by default in CI).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from xyz_agent_context.agent_framework import openai_agents_sdk as mod
from xyz_agent_context.agent_framework.openai_agents_sdk import (
    OpenAIAgentsSDK,
    _capability_key,
    _is_response_format_unsupported_error,
    _response_format_capability,
    _structured_output_blocklist,
)


class _Schema(BaseModel):
    is_continuous: bool = Field(description="True/False flag")
    reason: str = Field(description="One short sentence")


def _fake_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=0, completion_tokens=0)
    return resp


def _api_error(message: str, code: int = 400) -> Exception:
    """Build an exception shaped like an OpenAI / httpx error string."""
    return RuntimeError(f"Error code: {code} - {{'error': {{'message': {message!r}}}}}")


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset module-level state between tests."""
    _response_format_capability.clear()
    _structured_output_blocklist.clear()
    # Force the fallback path on every model these tests touch.
    yield
    _response_format_capability.clear()
    _structured_output_blocklist.clear()


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock()
    return client


# ── Helpers ─────────────────────────────────────────────────────────────


def _last_kwargs(client) -> dict:
    """Pull kwargs from the most recent fake client call."""
    return client.chat.completions.create.call_args.kwargs


def _all_kwargs(client) -> list[dict]:
    return [c.kwargs for c in client.chat.completions.create.call_args_list]


# ── Tests ───────────────────────────────────────────────────────────────


def test_unsupported_error_detector_recognises_netmind_wording():
    e = _api_error("This response_format type is unavailable", 400)
    assert _is_response_format_unsupported_error(e) is True


def test_unsupported_error_detector_recognises_response_format_token():
    e = _api_error("Invalid response_format value", 422)
    assert _is_response_format_unsupported_error(e) is True


def test_unsupported_error_detector_does_not_blame_unrelated_errors():
    e = RuntimeError("Rate limit hit")
    assert _is_response_format_unsupported_error(e) is False


@pytest.mark.asyncio
async def test_ladder_tries_json_schema_first(fake_client):
    """Happy path: json_schema works on first try, json_object is never tried."""
    fake_client.chat.completions.create.return_value = _fake_response(
        '{"is_continuous": true, "reason": "ok"}'
    )
    sdk = OpenAIAgentsSDK()
    result = await sdk._fallback_chat_completion(
        fake_client, "test-model-A",
        instructions="test", user_input="test",
        output_type=_Schema, max_tokens=200,
    )
    assert result.final_output.is_continuous is True
    calls = _all_kwargs(fake_client)
    assert len(calls) == 1, "should have made exactly one API call"
    rf = calls[0].get("response_format")
    assert rf is not None and rf["type"] == "json_schema", \
        f"first attempt must be json_schema, got {rf!r}"


@pytest.mark.asyncio
async def test_ladder_downgrades_json_schema_to_json_object_on_unsupported(
    fake_client,
):
    """V4-Flash-style: json_schema rejected → json_object accepted."""
    fake_client.chat.completions.create.side_effect = [
        _api_error("This response_format type is unavailable"),
        _fake_response('{"is_continuous": false, "reason": "differ"}'),
    ]
    sdk = OpenAIAgentsSDK()
    result = await sdk._fallback_chat_completion(
        fake_client, "test-model-B",
        instructions="i", user_input="u",
        output_type=_Schema, max_tokens=200,
    )
    assert result.final_output.is_continuous is False
    calls = _all_kwargs(fake_client)
    assert len(calls) == 2
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"
    # Cache learned the failure
    key = _capability_key("test-model-B")
    assert "json_schema" not in _response_format_capability[key]
    assert "json_object" in _response_format_capability[key]


@pytest.mark.asyncio
async def test_ladder_downgrades_all_the_way_to_prompt_only(fake_client):
    """Both json_schema and json_object rejected → prompt-only path."""
    fake_client.chat.completions.create.side_effect = [
        _api_error("This response_format type is unavailable"),
        _api_error("response_format not supported on this provider"),
        _fake_response('Sure: {"is_continuous": true, "reason": "x"}'),
    ]
    sdk = OpenAIAgentsSDK()
    result = await sdk._fallback_chat_completion(
        fake_client, "test-model-C",
        instructions="i", user_input="u",
        output_type=_Schema, max_tokens=200,
    )
    assert result.final_output.is_continuous is True
    calls = _all_kwargs(fake_client)
    assert len(calls) == 3
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"
    assert "response_format" not in calls[2]
    key = _capability_key("test-model-C")
    cached = _response_format_capability[key]
    assert "json_schema" not in cached
    assert "json_object" not in cached


@pytest.mark.asyncio
async def test_cache_short_circuits_subsequent_calls(fake_client):
    """After we learn json_schema is unsupported, future calls skip it."""
    fake_client.chat.completions.create.side_effect = [
        # First call: json_schema fails → json_object works
        _api_error("This response_format type is unavailable"),
        _fake_response('{"is_continuous": true, "reason": "first"}'),
        # Second call: should jump straight to json_object (only one API hop)
        _fake_response('{"is_continuous": false, "reason": "second"}'),
    ]
    sdk = OpenAIAgentsSDK()
    await sdk._fallback_chat_completion(
        fake_client, "test-model-D",
        instructions="i", user_input="u",
        output_type=_Schema, max_tokens=200,
    )
    await sdk._fallback_chat_completion(
        fake_client, "test-model-D",
        instructions="i", user_input="u",
        output_type=_Schema, max_tokens=200,
    )
    calls = _all_kwargs(fake_client)
    assert len(calls) == 3  # 2 from first call, 1 from second
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"
    assert calls[2]["response_format"]["type"] == "json_object"


@pytest.mark.asyncio
async def test_non_response_format_error_propagates_does_not_downgrade(
    fake_client,
):
    """A rate-limit / 5xx must NOT silently drop the level. The inner
    legacy ``max_completion_tokens → max_tokens`` retry will still fire
    (preserved from pre-2026-05-28 behaviour for older providers), so
    we feed it the same error twice and check the second propagates."""
    fake_client.chat.completions.create.side_effect = [
        RuntimeError("Rate limit exceeded"),
        RuntimeError("Rate limit exceeded"),
    ]
    sdk = OpenAIAgentsSDK()
    with pytest.raises(RuntimeError, match="Rate limit"):
        await sdk._fallback_chat_completion(
            fake_client, "test-model-E",
            instructions="i", user_input="u",
            output_type=_Schema, max_tokens=200,
        )
    # Capability cache must not have changed (no level was marked
    # unsupported — we did not see a response_format-shaped error).
    assert _capability_key("test-model-E") not in _response_format_capability


@pytest.mark.asyncio
async def test_strips_json_code_fences_in_extracted_output(fake_client):
    """DeepSeek-V3-style: returns valid JSON wrapped in ```json fences."""
    fake_client.chat.completions.create.return_value = _fake_response(
        '```json\n{"is_continuous": true, "reason": "ok"}\n```'
    )
    sdk = OpenAIAgentsSDK()
    result = await sdk._fallback_chat_completion(
        fake_client, "test-model-F",
        instructions="i", user_input="u",
        output_type=_Schema, max_tokens=200,
    )
    assert result.final_output.is_continuous is True


@pytest.mark.asyncio
async def test_no_output_type_skips_ladder(fake_client):
    """Plain text path: no response_format at all."""
    fake_client.chat.completions.create.return_value = _fake_response("hello world")
    sdk = OpenAIAgentsSDK()
    result = await sdk._fallback_chat_completion(
        fake_client, "test-model-G",
        instructions="i", user_input="u",
        output_type=None, max_tokens=200,
    )
    assert result.final_output == "hello world"
    calls = _all_kwargs(fake_client)
    assert len(calls) == 1
    assert "response_format" not in calls[0]


@pytest.mark.asyncio
async def test_invalid_json_raises_valueerror(fake_client):
    """If even the prompt-only level returns garbage, surface the error."""
    fake_client.chat.completions.create.side_effect = [
        _api_error("This response_format type is unavailable"),
        _api_error("response_format unsupported"),
        _fake_response("this is not JSON at all, just chitchat"),
    ]
    sdk = OpenAIAgentsSDK()
    with pytest.raises(ValueError, match="Could not extract JSON"):
        await sdk._fallback_chat_completion(
            fake_client, "test-model-H",
            instructions="i", user_input="u",
            output_type=_Schema, max_tokens=200,
        )
