"""
@file_name: test_im_dm_no_reply_fallback.py
@author:
@date: 2026-08-06
@description: Pins the no-reply fallback for 1:1 IM DMs.

Until 2026-08-06 the helper_llm no-reply recovery was gated to
``working_source == "chat"`` with the reason "job/lark have their own
reply tooling" (2026-05-12 design note). That premise conflated *having
a reply tool* with *the reply happening*: on an IM turn where the model
emitted plain text and never called the channel's send tool, the text
was discarded, the turn was recorded as an activity row, and the person
on WeChat received nothing at all. That is the 0802 report.

Two invariants matter most here:

1. A turn that DID call the channel reply tool must never be re-sent —
   ``_has_organic_reply`` used to look only for
   ``notify_owner``, so every successful WeChat turn
   read as "no reply" and would have been double-sent by this fallback.
2. Group rooms keep the silence default. The fallback is for 1:1 DMs
   only, where a real person is waiting on an answer.
"""
from __future__ import annotations

import pytest

from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _channel_turn_envelope,
    _deliver_im_fallback_reply,
    _has_organic_reply,
    _im_reply_tool_name,
    _should_run_helper_llm_fallback,
)
from narranexus.platform.channel.channel_sender_registry import ChannelSenderRegistry
from narranexus.platform.schema import (
    ErrorMessage,
    ProgressMessage,
    ProgressStatus,
)


# ---------- helpers ----------------------------------------------------


def _tool_progress(tool_name: str, **arguments) -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description=tool_name,
        status=ProgressStatus.COMPLETED,
        details={"tool_name": tool_name, "arguments": arguments or {}},
    )


def _wechat_send(text: str = "hi") -> ProgressMessage:
    return _tool_progress("mcp__wechat_module__wechat_send", text=text)


def _direct_notify(content: str = "fyi") -> ProgressMessage:
    return _tool_progress(
        "mcp__chat_module__notify_owner", content=content
    )


def _unrelated_tool() -> ProgressMessage:
    return _tool_progress("mcp__awareness_module__read_file", path="/x")


def _fatal() -> ErrorMessage:
    return ErrorMessage(
        error_type="sdk_crash", error_message="boom", severity="fatal"
    )


# ---------- 1. organic-reply detection is per-channel ------------------


class TestOrganicReplyIsChannelAware:
    """The double-send guard. If this regresses, every successful WeChat
    turn gets a second, helper-written reply."""

    def test_channel_send_tool_counts_as_a_reply(self):
        assert _has_organic_reply([_wechat_send()], working_source="wechat") is True

    def test_owner_notify_still_counts_on_chat(self):
        assert _has_organic_reply([_direct_notify()], working_source="chat") is True

    def test_unrelated_tool_is_not_a_reply(self):
        assert _has_organic_reply([_unrelated_tool()], working_source="wechat") is False

    def test_channel_tool_does_not_count_for_a_different_channel(self):
        """A wechat_send frame on a lark turn is not a lark reply — the
        registry lookup must be keyed by the turn's working_source."""
        assert _has_organic_reply([_wechat_send()], working_source="lark") is False

    def test_working_source_is_required(self):
        """No default on purpose (binding rule #2). A `"chat"` default is
        what let the severity call site keep the drift this function exists
        to remove: an IM turn that had replied via wechat_send then hit an
        executor-infra failure read as "never spoke" and was recorded as a
        hard fatal instead of recovered_after_reply."""
        with pytest.raises(TypeError):
            _has_organic_reply([_direct_notify()])  # type: ignore[call-arg]


# ---------- 2. the decision ------------------------------------------


class TestImDmFallbackDecision:
    def test_silent_dm_turn_runs_the_im_fallback(self):
        mode, reason = _should_run_helper_llm_fallback(
            "wechat", [_unrelated_tool()], None, is_direct_message=True
        )
        assert mode == "no_reply_im_dm"
        assert reason == ""

    def test_dm_turn_that_already_replied_is_left_alone(self):
        """The invariant that prevents double-sending."""
        mode, reason = _should_run_helper_llm_fallback(
            "wechat", [_wechat_send()], None, is_direct_message=True
        )
        assert mode is None
        assert reason == "already_replied_via_tool"

    def test_group_room_keeps_the_silence_default(self):
        mode, reason = _should_run_helper_llm_fallback(
            "lark", [_unrelated_tool()], None, is_direct_message=False
        )
        assert mode is None
        assert reason == "group_room_may_stay_silent"

    def test_message_bus_stays_excluded_even_in_a_dm(self):
        """Agent-to-agent loop prevention — the 2026-05-12 reason that is
        still valid. A bus peer is not a person waiting on WeChat."""
        mode, reason = _should_run_helper_llm_fallback(
            "message_bus", [_unrelated_tool()], None, is_direct_message=True
        )
        assert mode is None
        assert reason == "non_chat_trigger"

    def test_cancelled_dm_turn_is_honoured(self):
        class _Cancelled:
            is_cancelled = True

        mode, reason = _should_run_helper_llm_fallback(
            "wechat", [_unrelated_tool()], _Cancelled(), is_direct_message=True
        )
        assert mode is None
        assert reason == "cancellation_requested"

    def test_fatal_dm_turn_does_not_get_an_invented_reply(self):
        """Same honesty rule as chat: a turn that died mid-stream has only
        partial reasoning to summarise, and the IM path has no error
        surface of its own to correct it afterwards."""
        mode, reason = _should_run_helper_llm_fallback(
            "wechat", [_fatal()], None, is_direct_message=True
        )
        assert mode is None
        assert reason == "fatal_no_invented_reply"


# ---------- 3. the envelope actually arrives ---------------------------


class _FakeContextData:
    def __init__(self, extra_data):
        self.extra_data = extra_data


class _FakeContextOutput:
    """Shaped like ``ContextRuntimeOutput``: the envelope lives at
    ``.ctx_data.extra_data``, NOT on the pipeline ctx."""

    def __init__(self, extra_data):
        self.ctx_data = _FakeContextData(extra_data)


class TestChannelTurnEnvelope:
    """Regression guard for a wiring bug found in live testing on
    2026-08-06: the extractor was reading ``ctx.ctx_data``, but ContextData
    is built fresh inside step_3 and hangs off the ContextRuntime OUTPUT —
    ``ctx`` has no such attribute. It silently returned ``{}`` for every
    turn, so ``is_direct_message`` was always False and the whole IM DM
    fallback was dead code. A real Telegram DM proved it: the prompt
    carried the DM protocol, yet the decision logged
    ``group_room_may_stay_silent``.
    """

    def test_reads_the_envelope_off_context_output(self):
        out = _FakeContextOutput(
            {
                "channel_room_type": "Direct Message",
                "channel_reply_kwargs": {"context_token": "tok"},
                "channel_tag": {"channel": "wechat", "room_id": "peer"},
            }
        )
        env = _channel_turn_envelope(out)
        assert env["channel_room_type"] == "Direct Message"
        assert env["channel_reply_kwargs"] == {"context_token": "tok"}
        assert env["channel_tag"]["room_id"] == "peer"

    def test_object_without_ctx_data_yields_empty(self):
        """chat / job / bus turns — and the bug's symptom if it ever
        returns. Empty envelope = not a DM = no fallback."""
        assert _channel_turn_envelope(object()) == {}

    def test_none_ctx_data_yields_empty(self):
        class _NoData:
            ctx_data = None

        assert _channel_turn_envelope(_NoData()) == {}

    def test_malformed_extra_data_yields_empty(self):
        assert _channel_turn_envelope(_FakeContextOutput("not-a-dict")) == {}

    def test_missing_keys_degrade_to_not_a_dm(self):
        env = _channel_turn_envelope(_FakeContextOutput({"unrelated": 1}))
        assert env["channel_room_type"] == ""
        assert env["channel_reply_kwargs"] == {}
        assert env["channel_tag"] == {}


# ---------- 4. delivery ------------------------------------------------


@pytest.fixture
def fake_wechat_sender():
    """Register a recording sender for the duration of one test."""
    calls: list[dict] = []
    result: dict = {"success": True}

    async def _sender(agent_id, target_id, message, **kwargs):
        calls.append(
            {
                "agent_id": agent_id,
                "target_id": target_id,
                "message": message,
                "kwargs": kwargs,
            }
        )
        return result

    ChannelSenderRegistry.register("wechat", _sender)
    try:
        yield calls, result
    finally:
        ChannelSenderRegistry.unregister("wechat")


class TestImReplyToolName:
    def test_prefers_the_channel_send_tool(self):
        """Tagging the synthetic frame with notify_owner
        would file the reply as an OWNER notification and surface it in
        the owner's chat panel — a message the agent never sent them."""
        assert _im_reply_tool_name("wechat") == "wechat_send"

    def test_unknown_source_gets_a_self_describing_name(self):
        assert _im_reply_tool_name("nosuchchannel") == "nosuchchannel_send"


@pytest.mark.asyncio
class TestDelivery:
    async def test_delivers_through_the_registered_sender(self, fake_wechat_sender):
        calls, _ = fake_wechat_sender
        ok = await _deliver_im_fallback_reply(
            "wechat",
            {"channel": "wechat", "room_id": "wxid_peer", "agent_id": "agent_1"},
            {"context_token": "tok-123"},
            "here you go",
        )
        assert ok is True
        assert calls == [
            {
                "agent_id": "agent_1",
                "target_id": "wxid_peer",
                "message": "here you go",
                # iLink cannot address a conversation without the token —
                # it must survive the trip through the envelope.
                "kwargs": {"context_token": "tok-123"},
            }
        ]

    async def test_channel_refusal_is_not_reported_as_delivered(
        self, fake_wechat_sender
    ):
        """`success: False` must not produce a "replied" record — that is
        the same lie as the discarded plain text this fix removes."""
        _, result = fake_wechat_sender
        result["success"] = False
        ok = await _deliver_im_fallback_reply(
            "wechat", {"channel": "wechat", "room_id": "p"}, {}, "text"
        )
        assert ok is False

    async def test_missing_sender_is_survivable(self):
        ok = await _deliver_im_fallback_reply(
            "nosuchchannel", {"channel": "nosuchchannel", "room_id": "p"}, {}, "text"
        )
        assert ok is False

    async def test_missing_target_is_survivable(self, fake_wechat_sender):
        calls, _ = fake_wechat_sender
        ok = await _deliver_im_fallback_reply("wechat", {"channel": "wechat"}, {}, "t")
        assert ok is False
        assert calls == []

    async def test_sender_exception_does_not_escape(self):
        async def _boom(agent_id, target_id, message, **kwargs):
            raise RuntimeError("network down")

        ChannelSenderRegistry.register("wechat", _boom)
        try:
            ok = await _deliver_im_fallback_reply(
                "wechat", {"channel": "wechat", "room_id": "p"}, {}, "text"
            )
        finally:
            ChannelSenderRegistry.unregister("wechat")
        assert ok is False


class TestChatDecisionUnchanged:
    """Regression guard: the chat path's four outcomes must be untouched."""

    def test_chat_no_reply(self):
        assert _should_run_helper_llm_fallback("chat", [_unrelated_tool()], None) == (
            "no_reply",
            "",
        )

    def test_chat_already_replied(self):
        assert _should_run_helper_llm_fallback("chat", [_direct_notify()], None) == (
            None,
            "already_replied_via_tool",
        )

    def test_chat_after_error(self):
        assert _should_run_helper_llm_fallback("chat", [_fatal()], None) == (
            "after_error",
            "",
        )

    def test_chat_partial_reply_then_error(self):
        mode, reason = _should_run_helper_llm_fallback(
            "chat", [_direct_notify(), _fatal()], None
        )
        assert mode == "partial_reply_then_error"
