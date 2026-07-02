"""
@file_name: test_consolidate_systemic_errors.py
@author:
@date: 2026-06-11
@description: Regression tests for the 2026-06-11 P0 — cloud consolidation
ran without credentials, every batch 401'd, and the bisect-to-drop policy
destroyed 4599 facts. Systemic (content-independent) LLM errors must now
RAISE so the worker isolates the scope with facts preserved; content errors
keep the bisect-and-drop behavior.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import anthropic
import httpx
import openai
import pytest

from xyz_agent_context.memory._memory_impl.consolidate import (
    SystemicLLMError,
    _is_systemic_llm_error,
    consolidate,
)
from xyz_agent_context.memory.record import MemoryRecord


def _fact(text: str) -> MemoryRecord:
    return MemoryRecord(
        agent_id="agent_t", scope_type="agent", scope_id="", kind="observation",
        content_text=text, subtype="world",
    )


def _auth_error() -> openai.AuthenticationError:
    req = httpx.Request("POST", "https://api.test/v1/chat/completions")
    resp = httpx.Response(401, request=req)
    return openai.AuthenticationError("Error code: 401", response=resp, body=None)


def _anthropic_conn_error() -> anthropic.APIConnectionError:
    """A connection failure carries NO status_code — only the isinstance
    check can catch it. This is the anthropic leg of §5."""
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=req)


def _anthropic_auth_error() -> anthropic.AuthenticationError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(401, request=req)
    return anthropic.AuthenticationError("Error code: 401", response=resp, body=None)


class _WrappedError(RuntimeError):
    """Simulates the Agents SDK wrapping the underlying client error."""


def test_classifier_catches_direct_auth_error():
    assert _is_systemic_llm_error(_auth_error()) is True


def test_classifier_walks_the_cause_chain():
    wrapped = _WrappedError("agents sdk failed")
    wrapped.__cause__ = _auth_error()
    assert _is_systemic_llm_error(wrapped) is True


def test_classifier_catches_status_code_attr():
    e = RuntimeError("provider exploded")
    e.status_code = 503  # type: ignore[attr-defined]
    assert _is_systemic_llm_error(e) is True


def test_classifier_lets_content_errors_through():
    assert _is_systemic_llm_error(ValueError("model returned garbage JSON")) is False


def test_classifier_catches_anthropic_connection_error():
    # §5 regression: anthropic.APIConnectionError is neither an openai.*
    # class nor does it carry a status_code, so before the fix it escaped
    # BOTH checks → bisect-drop fact loss for anthropic-helper users.
    assert _is_systemic_llm_error(_anthropic_conn_error()) is True


def test_classifier_catches_anthropic_auth_in_cause_chain():
    wrapped = _WrappedError("helper sdk failed")
    wrapped.__cause__ = _anthropic_auth_error()
    assert _is_systemic_llm_error(wrapped) is True


@pytest.mark.asyncio
async def test_systemic_error_raises_and_preserves_facts():
    """A 401 must NOT bisect-and-drop — it raises so the worker isolates
    the scope and the raw facts survive for a later retry."""
    sdk = AsyncMock()
    sdk.llm_function = AsyncMock(side_effect=_auth_error())
    repo = AsyncMock()

    with pytest.raises(SystemicLLMError):
        await consolidate(
            repo,
            agent_id="agent_t", scope_type="agent", scope_id="", kind="observation",
            new_facts=[_fact("a"), _fact("b"), _fact("c")],
            existing=[], sdk=sdk,
        )

    # No bisection storm: one failed call, not 2N-1.
    assert sdk.llm_function.await_count == 1
    # Nothing was written or tombstoned by consolidate itself.
    repo.upsert.assert_not_awaited()
    repo.tombstone.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_error_still_bisects_to_isolation():
    """Pre-existing policy unchanged: a content-level failure bisects down
    and drops only the unconsolidatable unit, never raising."""
    sdk = AsyncMock()
    sdk.llm_function = AsyncMock(side_effect=ValueError("bad JSON"))
    repo = AsyncMock()

    changed = await consolidate(
        repo,
        agent_id="agent_t", scope_type="agent", scope_id="", kind="observation",
        new_facts=[_fact("a"), _fact("b")],
        existing=[], sdk=sdk,
    )

    assert changed == 0
    # Full batch, then each half: 3 calls for 2 facts.
    assert sdk.llm_function.await_count == 3
