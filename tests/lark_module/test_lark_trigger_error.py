"""
@file_name: test_lark_trigger_error.py
@author: Bin Liang
@date: 2026-04-20
@description: Error-path tests for LarkTrigger._build_and_run_agent (Bug 2).

Before the fix: the loop only handled `MessageType.AGENT_RESPONSE`, so
`ErrorMessage` events were silently dropped — the Lark sender saw radio
silence when the agent failed to load its LLM config.

After the fix: the trigger uses `collect_run`, detects `.is_error`, sends
a user-friendly message through lark-cli, and returns that text so the
Inbox row reflects what actually happened.

The `format_lark_error_reply` helper is pure text rendering so we also
test it directly as the seam most other trigger/consumer code will rely
on.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.agent_runtime.run_collector import RunError
from xyz_agent_context.module.lark_module.lark_trigger import (
    LarkTrigger,
    format_lark_error_reply,
)
from xyz_agent_context.schema.runtime_message import MessageType


# -------- pure formatter --------------------------------------------------

def test_format_error_for_system_unavailable_is_user_friendly():
    text = format_lark_error_reply(
        RunError(error_type="SystemDefaultUnavailable", error_message="quota exhausted")
    )
    assert "free-quota" in text.lower() or "quota" in text.lower()
    # Does NOT leak developer-language like "SlotConfig" / "provider_id"
    assert "slot" not in text.lower()
    assert "provider_id" not in text


def test_format_error_for_not_configured_tells_sender_to_contact_owner():
    text = format_lark_error_reply(
        RunError(error_type="LLMConfigNotConfigured", error_message="'agent' slot missing")
    )
    assert "owner" in text.lower()


def test_format_error_for_unknown_type_falls_back_to_generic():
    text = format_lark_error_reply(
        RunError(error_type="TimeoutError", error_message="cli did not respond for 1200s")
    )
    assert "internal error" in text.lower() or "try again" in text.lower()


# -------- end-to-end on _build_and_run_agent -----------------------------

class _FakeCred:
    """Minimal stand-in for LarkCredential."""
    agent_id = "agent_test"
    app_id = "cli_test"
    app_secret_ref = "ref"
    brand = "lark"
    profile_name = "agent_test_profile"


class _FakeCtxBuilder:
    """Just enough to let _build_and_run_agent finish setup; the actual
    prompt content doesn't matter because we control what the runtime
    yields."""

    def __init__(self, event, credential, cli, agent_id):
        self.event = event
        self.credential = credential

    async def build_prompt(self, _history_config):
        return "user prompt goes here"

    async def build_retrieval_anchor(self):
        # Real builders inherit this from ChannelContextBuilderBase; the fake
        # provides it so _build_and_run_agent can attach the narrative anchor.
        return "[From Alice] user prompt goes here"


class _ErrorRuntime:
    """AgentRuntime stand-in that yields one ERROR message and stops."""

    def __init__(self, err_type: str, err_msg: str):
        self._err_type = err_type
        self._err_msg = err_msg

    def run(self, **_kwargs) -> AsyncIterator:
        async def _gen():
            yield SimpleNamespace(
                message_type=MessageType.ERROR,
                error_type=self._err_type,
                error_message=self._err_msg,
            )
        return _gen()


@pytest.mark.asyncio
async def test_build_and_run_agent_sends_friendly_error_reply(monkeypatch):
    """On ERROR, the trigger must call lark-cli send_message with a
    user-friendly text and return the same text."""
    from xyz_agent_context.module.lark_module import lark_trigger as lt_mod

    # 1) Stub out AgentRuntime so we don't need a real DB / LLM / MCP.
    monkeypatch.setattr(
        lt_mod, "AgentRuntime",
        lambda **_kw: _ErrorRuntime(
            err_type="SystemDefaultUnavailable",
            err_msg="quota exhausted",
        ),
    )
    # 2) Stub LarkContextBuilder — we only need build_prompt to return text.
    monkeypatch.setattr(lt_mod, "LarkContextBuilder", _FakeCtxBuilder)
    # 3) agent_id → owner_user_id lookup happens via self._db.get_one("agents",...).
    #    Fake a DB row.
    db = SimpleNamespace(
        get_one=AsyncMock(return_value={"created_by": "owner_user_123"}),
    )

    trigger = LarkTrigger()
    trigger._db = db
    trigger._cli = SimpleNamespace(send_message=AsyncMock())

    output = await trigger._build_and_run_agent(
        cred=_FakeCred(),
        event={"chat_type": "p2p", "chat_name": "Test"},
        chat_id="oc_test",
        sender_id="ou_alice",
        sender_name="Alice",
        text="hi bot",
        message_id="msg_1",
    )

    # Output is the friendly text (so the inbox row shows it).
    assert "⚠️" in output
    assert "quota" in output.lower() or "free-quota" in output.lower()

    # lark-cli was called to deliver the reply to the chat.
    trigger._cli.send_message.assert_awaited_once()
    call_kwargs = trigger._cli.send_message.await_args.kwargs
    assert call_kwargs.get("chat_id") == "oc_test"
    assert "⚠️" in call_kwargs.get("text", "")


@pytest.mark.asyncio
async def test_build_and_run_agent_swallows_lark_send_failure(monkeypatch):
    """If the error-reply itself fails to send, _build_and_run_agent must
    still return the friendly text so the inbox row is written correctly."""
    from xyz_agent_context.module.lark_module import lark_trigger as lt_mod

    monkeypatch.setattr(
        lt_mod, "AgentRuntime",
        lambda **_kw: _ErrorRuntime(
            err_type="LLMConfigNotConfigured",
            err_msg="'agent' slot missing",
        ),
    )
    monkeypatch.setattr(lt_mod, "LarkContextBuilder", _FakeCtxBuilder)

    db = SimpleNamespace(
        get_one=AsyncMock(return_value={"created_by": "owner_user_123"}),
    )

    trigger = LarkTrigger()
    trigger._db = db
    trigger._cli = SimpleNamespace(
        send_message=AsyncMock(side_effect=RuntimeError("network down"))
    )

    output = await trigger._build_and_run_agent(
        cred=_FakeCred(),
        event={"chat_type": "p2p", "chat_name": "Test"},
        chat_id="oc_test",
        sender_id="ou_alice",
        sender_name="Alice",
        text="hi",
        message_id="msg_2",
    )

    assert "⚠️" in output
    # The send attempt happened even though it failed.
    trigger._cli.send_message.assert_awaited_once()
