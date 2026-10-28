"""
@file_name: test_voice_register.py
@date: 2026-08-06
@description: F28 voice register — builder branch, speak tool, expressive order.

Locks:
- rtc_voice in ParsedMessage.raw flips get_message_info to the voice
  register: send_tool_name "speak"; the instruction demands spoken
  short sentences, speak-first-multi-call, a concrete spoken preannounce
  before other tools, and bans markdown / metadata read-aloud; the
  per-turn voice_instructions ride along verbatim.
- No rtc_voice -> the narra_reply instruction is EXACTLY as before
  (normal-path regression pin).
- speak is a registered MCP tool (stub executor, narra_reply pattern).
- On a voice turn the expressive surface leads with fully-qualified
  speak; a normal turn's surface is unchanged (no dead speak entry).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from narranexus_plugins.narramessenger_module.narramessenger_context_builder import (
    NarramessengerContextBuilder,
)
from narranexus_plugins.narramessenger_module.narramessenger_module import (
    NarramessengerModule,
)
from narranexus_plugins.narramessenger_module import _narramessenger_mcp_tools
from narranexus.platform.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)

RTC = {
    "rtc_session_id": "rtc-s1",
    "turn_id": "t1",
    "invocation_id": "inv1",
    "agent_profile_id": "prof1",
    "voice_instructions": "Reply for a real-time voice call.",
}


def _msg(raw=None) -> ParsedMessage:
    return ParsedMessage(
        message_id="$e1",
        chat_id="!call:h",
        sender_id="@human:h",
        sender_name="Caller",
        content="What is the weather today?",
        content_type=MessageContentType.TEXT,
        chat_type=ChatType.PRIVATE,
        timestamp_ms=1,
        raw=raw or {},
    )


def _builder(raw=None) -> NarramessengerContextBuilder:
    cred = NarramessengerCredential(
        agent_id="agent_v",
        bearer_token="tok",
        matrix_homeserver_url="https://h",
        matrix_user_id="@agent:h",
        matrix_access_token="syt",
    )
    return NarramessengerContextBuilder(_msg(raw), cred, "agent_v")


@pytest.mark.asyncio
async def test_voice_register_swaps_reply_tool_and_instruction():
    info = await _builder(raw={"rtc_voice": RTC}).get_message_info()
    assert info["send_tool_name"] == "speak"
    inst = info["reply_instruction"]
    assert "speak(" in inst
    assert "voice call" in inst.lower()
    # Multi-call + preannounce discipline + output bans.
    assert "several short" in inst.lower()  # long answers segment into calls
    assert "private notes" in inst.lower()  # prose is never delivered
    assert "before" in inst.lower()  # preannounce before other tools
    assert "markdown" in inst.lower()
    # On a call, EVERY utterance gets a spoken response — the DM
    # protocol's acknowledgment carve-out does not apply to voice
    # (2026-08-13: a goodbye turn went silent; the caller hears a
    # broken line, not polite restraint).
    assert "goodbye" in inst.lower()
    # Per-turn instructions ride along verbatim.
    assert "Reply for a real-time voice call." in inst


@pytest.mark.asyncio
async def test_normal_turn_instruction_unchanged():
    info = await _builder().get_message_info()
    assert info["send_tool_name"] == "narra_reply"
    assert info["reply_instruction"].startswith(
        'call `narra_reply(text="YOUR_REPLY")`'
    )
    assert "speak" not in info["reply_instruction"]


def test_speak_tool_registered():
    class FakeMCP:
        def __init__(self):
            self.names = []

        def tool(self):
            def deco(fn):
                self.names.append(fn.__name__)
                return fn

            return deco

    mcp = FakeMCP()
    _narramessenger_mcp_tools.register_narramessenger_mcp_tools(mcp)
    assert "speak" in mcp.names
    assert "speak" in NarramessengerModule.all_tool_names


@pytest.mark.asyncio
async def test_expressive_surface_leads_with_speak_on_voice_turns(monkeypatch):
    module = NarramessengerModule.__new__(NarramessengerModule)

    async def _bound():
        return True

    monkeypatch.setattr(module, "is_bound", _bound, raising=False)

    voice_ctx = SimpleNamespace(
        working_source="narramessenger", extra_data={"rtc_voice": RTC}
    )
    tools = await module.expressive_tools(voice_ctx)
    assert tools[0] == "mcp__narramessenger_module__speak"
    assert "mcp__narramessenger_module__narra_reply" in tools

    normal_ctx = SimpleNamespace(working_source="narramessenger", extra_data={})
    normal = await module.expressive_tools(normal_ctx)
    assert "mcp__narramessenger_module__speak" not in normal
    assert normal[0] == "mcp__narramessenger_module__narra_reply"
