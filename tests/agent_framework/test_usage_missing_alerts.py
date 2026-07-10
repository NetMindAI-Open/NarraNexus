"""
@file_name: test_usage_missing_alerts.py
@author: NarraNexus
@date: 2026-07-03
@description: Phase 0 (module H) — de-silence missing token usage.

Every helper-SDK cost site records tokens only when input+output > 0. When a
live cost context is present but the provider returned no usage, the tokens go
UNRECORDED — historically a silent miss (exactly how the consolidation-worker
hole hid). The SDKs now call cost_tracker.warn_missing_usage on that branch.

These tests exercise the record helpers on each SDK with a usage-less response
and assert the warning fires (and, positively, that a normal usage response
records cost and does NOT warn). We build the SDK objects via object.__new__ to
skip network/config-heavy __init__ — only the record path is under test.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import xyz_agent_context.agent_framework.gemini_api_sdk as gem
import xyz_agent_context.agent_framework.openai_agents_sdk as oai
import xyz_agent_context.utils.cost_tracker as ct
from xyz_agent_context.utils.cost_tracker import (
    clear_cost_context,
    set_cost_context,
)


@pytest.fixture(autouse=True)
def _reset_ctx():
    clear_cost_context()
    yield
    clear_cost_context()


# ── shared helper contract (used identically by all 6 helper-SDK cost sites) ──

def test_warn_missing_usage_warns_once_and_names_context():
    with patch.object(ct.logger, "warning") as w:
        ct.warn_missing_usage("AnthropicHelper-Stream", "claude-x", "llm_stream")
    w.assert_called_once()
    msg = w.call_args[0][0]
    assert "AnthropicHelper-Stream" in msg and "claude-x" in msg and "llm_stream" in msg


def test_warn_missing_usage_never_raises():
    # Observability only — must not become flow control.
    with patch.object(ct.logger, "warning", side_effect=RuntimeError("logger down")):
        try:
            ct.warn_missing_usage("X", "m", "llm_function")
        except RuntimeError:
            raise AssertionError("warn_missing_usage must never propagate")


# ── OpenAI Agents SDK: _record_cost ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_openai_record_cost_warns_when_no_usage():
    sdk = object.__new__(oai.OpenAIAgentsSDK)
    result = MagicMock()
    result.raw_responses = []  # no usage anywhere → 0 tokens
    with patch.object(oai, "record_cost", new=AsyncMock()) as rc, \
         patch.object(oai, "warn_missing_usage") as warn:
        await sdk._record_cost(result, "gpt-x", "agt_1", MagicMock())
    warn.assert_called_once()
    rc.assert_not_awaited()


@pytest.mark.asyncio
async def test_openai_record_cost_records_when_usage_present():
    sdk = object.__new__(oai.OpenAIAgentsSDK)
    usage = MagicMock(input_tokens=10, output_tokens=5)
    result = MagicMock()
    result.raw_responses = [MagicMock(usage=usage)]
    with patch.object(oai, "record_cost", new=AsyncMock()) as rc, \
         patch.object(oai, "warn_missing_usage") as warn:
        await sdk._record_cost(result, "gpt-x", "agt_1", MagicMock())
    rc.assert_awaited_once()
    warn.assert_not_called()


@pytest.mark.asyncio
async def test_openai_record_cost_uses_ambient_context_when_no_explicit_db():
    """When called without explicit agent_id/db it resolves the ambient cost
    context — and still warns on empty usage (the worker/consolidation path)."""
    sdk = object.__new__(oai.OpenAIAgentsSDK)
    set_cost_context("agt_ambient", MagicMock())
    result = MagicMock()
    result.raw_responses = []
    with patch.object(oai, "warn_missing_usage") as warn:
        await sdk._record_cost(result, "gpt-x", None, None)
    warn.assert_called_once()


@pytest.mark.asyncio
async def test_openai_record_cost_silent_when_no_cost_context():
    """No explicit params and no ambient context → not our turn to account;
    neither record nor warn."""
    sdk = object.__new__(oai.OpenAIAgentsSDK)
    result = MagicMock()
    result.raw_responses = []
    with patch.object(oai, "warn_missing_usage") as warn:
        await sdk._record_cost(result, "gpt-x", None, None)
    warn.assert_not_called()


# ── Gemini SDK: _record_usage ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gemini_record_usage_warns_when_no_usage():
    sdk = object.__new__(gem.GeminiAPISDK)
    sdk.model = "gemini-2.5-flash"
    response = object()  # no usage_metadata attribute
    with patch.object(gem, "record_cost", new=AsyncMock()) as rc, \
         patch.object(gem, "warn_missing_usage") as warn:
        await sdk._record_usage(response, "agt_1", MagicMock())
    warn.assert_called_once()
    rc.assert_not_awaited()


@pytest.mark.asyncio
async def test_gemini_record_usage_records_when_usage_present():
    sdk = object.__new__(gem.GeminiAPISDK)
    sdk.model = "gemini-2.5-flash"
    response = MagicMock()
    response.usage_metadata = MagicMock(prompt_token_count=12, candidates_token_count=3)
    with patch.object(gem, "record_cost", new=AsyncMock()) as rc, \
         patch.object(gem, "warn_missing_usage") as warn:
        await sdk._record_usage(response, "agt_1", MagicMock())
    rc.assert_awaited_once()
    warn.assert_not_called()


@pytest.mark.asyncio
async def test_gemini_record_usage_silent_when_no_cost_context():
    sdk = object.__new__(gem.GeminiAPISDK)
    sdk.model = "gemini-2.5-flash"
    response = object()
    with patch.object(gem, "warn_missing_usage") as warn:
        await sdk._record_usage(response, None, None)
    warn.assert_not_called()
