"""
@file_name: test_session_continuity_anchor.py
@author: Bin Liang
@date: 2026-05-20
@description: Pins the 2026-05-20 short-term-memory continuity fix (#1).

Three behaviors that together make a short reply ("好"/"yes") keep its
conversational anchor:

1. Sessions never expire — an existing session is reused regardless of how
   long the user was idle (the former 10-min SESSION_TIMEOUT is gone). The
   session is the chat-box continuity anchor; a user may reply to a visible
   message hours/days later.

2. `_turn_delivered_user_message` detects whether a turn delivered a
   user-visible message (via send_message_to_user_directly / IM reply tools),
   so step_4 can anchor the session even for background-trigger turns that
   messaged the user.

3. ContinuityDetector runs (does not short-circuit to "new_session") when the
   only anchor is `last_response` — i.e. the user is replying to a message the
   agent sent proactively, with no preceding user query.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xyz_agent_context.narrative.models import ConversationSession
from xyz_agent_context.narrative.session_service import SessionService
from xyz_agent_context.narrative._narrative_impl.continuity import ContinuityDetector
from xyz_agent_context.agent_runtime._agent_runtime_steps.step_4_persist_results import (
    _turn_delivered_user_message,
)
from xyz_agent_context.schema import ProgressMessage, ProgressStatus


# --------------------------------------------------------------------------
# 1. Sessions never expire (timeout removed)
# --------------------------------------------------------------------------

async def test_session_reused_after_long_idle(tmp_path):
    """A session idle far longer than the old 10-min timeout is still reused
    (same session_id), loaded from file by a fresh service instance."""
    svc = SessionService(session_dir=str(tmp_path))
    s1 = await svc.get_or_create_session(user_id="u1", agent_id="agent_x")

    # Simulate a long idle gap — way beyond the old 600s timeout.
    s1.last_query_time = datetime.now(timezone.utc) - timedelta(days=3)
    s1.last_query = "earlier question"
    await svc.save_session(s1)

    # Fresh service (cold cache) must load from file and REUSE, not recreate.
    svc2 = SessionService(session_dir=str(tmp_path))
    s2 = await svc2.get_or_create_session(user_id="u1", agent_id="agent_x")

    assert s2.session_id == s1.session_id
    assert s2.last_query == "earlier question"


async def test_cleanup_expired_sessions_is_noop(tmp_path):
    """cleanup_expired_sessions never evicts now — always returns 0."""
    svc = SessionService(session_dir=str(tmp_path))
    s = await svc.get_or_create_session(user_id="u1", agent_id="agent_x")
    s.last_query_time = datetime.now(timezone.utc) - timedelta(days=30)
    await svc.save_session(s)

    assert await svc.cleanup_expired_sessions() == 0
    # Session still retrievable afterwards.
    again = await svc.get_or_create_session(user_id="u1", agent_id="agent_x")
    assert again.session_id == s.session_id


# --------------------------------------------------------------------------
# 2. _turn_delivered_user_message
# --------------------------------------------------------------------------

def _reply_pm(content: str) -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="reply",
        description="",
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": "mcp__chat_module__send_message_to_user_directly",
            "arguments": {"content": content},
        },
    )


def test_delivered_true_when_message_sent_even_from_job():
    pm = _reply_pm("Confirm and I'll delete all 7.")
    # A JOB-source turn that called send_message_to_user_directly counts.
    assert _turn_delivered_user_message([pm], "job") is True


def test_delivered_false_when_no_reply_tool():
    other = ProgressMessage(
        step="3.4.1",
        title="tool",
        description="",
        status=ProgressStatus.COMPLETED,
        details={"tool_name": "mcp__some_module__do_thing", "arguments": {}},
    )
    assert _turn_delivered_user_message([other], "job") is False
    assert _turn_delivered_user_message([], "job") is False


def test_delivered_false_when_empty_content():
    assert _turn_delivered_user_message([_reply_pm("")], "chat") is False


# --------------------------------------------------------------------------
# 3. Continuity runs when only last_response is set (proactive message)
# --------------------------------------------------------------------------

def _session(last_query: str, last_response: str) -> ConversationSession:
    now = datetime.now(timezone.utc)
    return ConversationSession(
        session_id="sess_test",
        user_id="u1",
        agent_id="agent_x",
        created_at=now,
        last_query_time=now,
        last_query=last_query,
        last_response=last_response,
        current_narrative_id="nar_abc",
    )


async def test_continuity_short_circuits_only_when_no_visible_history(monkeypatch):
    detector = ContinuityDetector()

    called = {"n": 0}

    async def _fake_call_llm(**kwargs):
        called["n"] += 1
        from xyz_agent_context.narrative.models import ContinuityResult
        return ContinuityResult(is_continuous=True, confidence=0.9, reason="stub")

    monkeypatch.setattr(detector, "_call_llm", _fake_call_llm)

    # Both empty → genuinely new session, no LLM call.
    res = await detector.detect("好", _session("", ""))
    assert res.reason == "new_session"
    assert called["n"] == 0

    # Only last_response set (agent messaged the user proactively, no prior
    # user query) → must NOT short-circuit; continuity LLM is consulted.
    res2 = await detector.detect("好", _session("", "Confirm and I'll delete all 7."))
    assert res2.reason != "new_session"
    assert called["n"] == 1
