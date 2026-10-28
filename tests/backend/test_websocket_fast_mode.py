"""
@file_name: test_websocket_fast_mode.py
@date: 2026-08-14
@description: WS fresh-run fast_mode — request field + drive kwargs wiring.

Locks:
  * AgentRunRequest.fast_mode defaults False (absent field = today's path)
    and parses an explicit true.
  * _fresh_run_drive_kwargs forwards the flag verbatim and preserves the
    pre-existing kwargs contract (trigger_id prefix, sender_user_id,
    retrieval_anchor, conditional attachments).
"""
from __future__ import annotations

from backend.routes.websocket import AgentRunRequest, _fresh_run_drive_kwargs


def _request(**overrides) -> AgentRunRequest:
    base = {"agent_id": "a1", "user_id": "u1", "input_content": "hi"}
    base.update(overrides)
    return AgentRunRequest(**base)


def test_fast_mode_defaults_false():
    assert _request().fast_mode is False


def test_fast_mode_parses_true():
    assert _request(fast_mode=True).fast_mode is True


def test_drive_kwargs_forward_fast_mode():
    kwargs = _fresh_run_drive_kwargs(
        _request(fast_mode=True),
        session_id="sess12345678",
        working_source="chat",
        mcp_servers={},
    )
    assert kwargs["fast_mode"] is True


def test_drive_kwargs_default_fast_mode_false():
    kwargs = _fresh_run_drive_kwargs(
        _request(),
        session_id="sess12345678",
        working_source="chat",
        mcp_servers={},
    )
    assert kwargs["fast_mode"] is False


def test_drive_kwargs_preserve_existing_contract():
    kwargs = _fresh_run_drive_kwargs(
        _request(attachments=[{"file_id": "f1"}]),
        session_id="sess12345678",
        working_source="chat",
        mcp_servers={"srv": {}},
    )
    assert kwargs["agent_id"] == "a1"
    assert kwargs["user_id"] == "u1"
    assert kwargs["input_content"] == "hi"
    assert kwargs["working_source"] == "chat"
    assert kwargs["pass_mcp_servers"] == {"srv": {}}
    extra = kwargs["trigger_extra_data"]
    assert extra["trigger_id"] == "ws_sess1234"
    assert extra["sender_user_id"] == "u1"
    assert extra["retrieval_anchor"] == "hi"
    assert extra["attachments"] == [{"file_id": "f1"}]


def test_drive_kwargs_omit_attachments_when_absent():
    kwargs = _fresh_run_drive_kwargs(
        _request(),
        session_id="sess12345678",
        working_source="chat",
        mcp_servers={},
    )
    assert "attachments" not in kwargs["trigger_extra_data"]
