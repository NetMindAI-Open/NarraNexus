"""
@file_name: test_platform_reply_reaches_the_inbox.py
@author:
@date: 2026-08-07
@description: A platform-written reply must not be recorded as silence.

`step_3`'s 1:1 DM fallback delivers through `ChannelSenderRegistry` and
records a synthetic tool-call frame. `run_collector` folds that frame back
into `result.raw_items`, where the trigger's `extract_output` reads it — and
every channel's extractor scrapes only its OWN tool's argument shape:
WeChat's reads `arguments["text"]`, Lark's requires `+messages-send` inside
`command`. Both come back empty and fall through to
`CHANNEL_SILENT_SENTINEL`.

That sentinel is what `ChannelInboxWriter` persists as the turn's
agent_response. On WeChat it is not cosmetic:
`WeChatContextBuilder.get_conversation_history` reads recent turns back out
of `bus_messages`, so the next turn's Conversation History would show the bot
saying "(stayed silent)" for a reply the person actually received.

Fixed once in `ChannelTriggerBase.resolve_agent_response`, which consults
`platform_written_reply` before the per-channel extractor — the same
precedence the handler layer uses, for the same reason.
"""
from __future__ import annotations

import json

import pytest

from narranexus.platform.channel.channel_trigger_base import (
    CHANNEL_SILENT_SENTINEL,
    ChannelTriggerBase,
)
from narranexus.platform.channel.message_source_handler import PLATFORM_REPLY_TEXT_KEY

PLATFORM_TEXT = "好的，这是你要的答案。"


class _Result:
    def __init__(self, raw_items, output_text=""):
        self.raw_items = raw_items
        self.output_text = output_text
        self.error = None


def _platform_frame(text=PLATFORM_TEXT, *, as_json_string=False):
    """The frame step_3 synthesises after a successful platform delivery."""
    args = {"content": text, PLATFORM_REPLY_TEXT_KEY: text}
    return {
        "item": {
            "type": "tool_call_item",
            "tool_name": "wechat_send",
            "arguments": json.dumps(args) if as_json_string else args,
        }
    }


def _organic_wechat_frame(text="model's own reply"):
    return {
        "item": {
            "type": "tool_call_item",
            "tool_name": "mcp__wechat_module__wechat_send",
            "arguments": {"text": text},
        }
    }


class _Trigger(ChannelTriggerBase):
    channel_name = "faux"
    working_source = "faux"

    async def load_active_credentials(self):  # pragma: no cover
        return []

    async def subscribe(self, credential):  # pragma: no cover
        return None

    async def connect(self, credential):  # pragma: no cover
        return None

    def is_echo(self, event, credential):  # pragma: no cover
        return False

    def parse_event(self, event, credential):  # pragma: no cover
        return None

    async def resolve_sender_name(self, message, credential):  # pragma: no cover
        return ""

    def create_context_builder(self, message, credential, agent_id):  # pragma: no cover
        return None

    def extract_output(self, result, message, credential) -> str:
        """Mimics every real channel: scrapes its own argument shape and
        reports silence when it finds nothing it recognises."""
        for raw in getattr(result, "raw_items", []) or []:
            # Real channel extractors all guard this (see
            # wechat_trigger.extract_output); the double mirrors them.
            if not isinstance(raw, dict):
                continue
            item = raw.get("item", {})
            if not isinstance(item, dict):
                continue
            args = item.get("arguments", {})
            if isinstance(args, dict) and args.get("text"):
                return args["text"]
        return CHANNEL_SILENT_SENTINEL


@pytest.fixture
def trigger():
    return _Trigger.__new__(_Trigger)


class TestPlatformReplyWins:
    def test_platform_frame_is_recorded_verbatim(self, trigger):
        out = trigger.resolve_agent_response(
            _Result([_platform_frame()]), None, None
        )
        assert out == PLATFORM_TEXT
        assert out != CHANNEL_SILENT_SENTINEL

    def test_json_encoded_arguments_are_handled(self, trigger):
        """raw_items arguments arrive as a JSON string on some paths."""
        out = trigger.resolve_agent_response(
            _Result([_platform_frame(as_json_string=True)]), None, None
        )
        assert out == PLATFORM_TEXT

    def test_without_the_fix_this_channel_would_report_silence(self, trigger):
        """Pins the failure mode itself: the channel extractor alone cannot
        read the platform frame."""
        assert (
            trigger.extract_output(_Result([_platform_frame()]), None, None)
            == CHANNEL_SILENT_SENTINEL
        )


class TestOrganicRepliesUnaffected:
    def test_channel_extractor_still_wins_when_the_model_replied(self, trigger):
        out = trigger.resolve_agent_response(
            _Result([_organic_wechat_frame()]), None, None
        )
        assert out == "model's own reply"

    def test_genuinely_silent_turn_still_reports_silence(self, trigger):
        out = trigger.resolve_agent_response(_Result([]), None, None)
        assert out == CHANNEL_SILENT_SENTINEL


class TestMalformedFramesAreSurvivable:
    @pytest.mark.parametrize(
        "raw_items",
        [
            [{"item": {"type": "tool_call_item", "arguments": "not-json"}}],
            [{"item": {"type": "tool_call_item", "arguments": ["list"]}}],
            [{"item": {"type": "message_item", "arguments": {PLATFORM_REPLY_TEXT_KEY: "x"}}}],
            [{"not_an_item": 1}],
            ["garbage"],
        ],
    )
    def test_no_crash_and_no_false_positive(self, trigger, raw_items):
        out = trigger.resolve_agent_response(_Result(raw_items), None, None)
        assert out == CHANNEL_SILENT_SENTINEL

    def test_blank_platform_text_is_not_treated_as_a_reply(self, trigger):
        out = trigger.resolve_agent_response(
            _Result([_platform_frame(text="   ")]), None, None
        )
        assert out == CHANNEL_SILENT_SENTINEL


# ---------- the streaming path (NarraMessenger's default) ---------------


class TestStreamingPathCapturesPlatformReply:
    """`MatrixTrigger._build_and_run_agent_streaming` is the DEFAULT
    NarraMessenger path and never calls `resolve_agent_response` — it
    returns text captured mid-stream. So the base-class fix above does not
    reach it: the frame step_3 synthesises carries `content` +
    PLATFORM_REPLY_TEXT_KEY and no `text`, which is the only key the stream
    handler looked at. The turn finalised as silent and the inbox recorded
    "" for a message the person had received.

    Captured into its own state field, never `narra_reply_text`: finalize
    fresh-sends that one to the room, so reusing it would deliver the same
    message twice.
    """

    @staticmethod
    def _handler_and_state():
        from narranexus_plugins.narramessenger_module.matrix_trigger import (
            MatrixTrigger,
            _StreamReplyState,
        )

        return MatrixTrigger.__new__(MatrixTrigger), _StreamReplyState()

    @staticmethod
    def _progress(arguments, tool_name="wechat_send"):
        from narranexus.platform.schema import ProgressMessage, ProgressStatus

        return ProgressMessage(
            step="3.4.fallback",
            title="Reply",
            description=tool_name,
            status=ProgressStatus.COMPLETED,
            details={"tool_name": tool_name, "arguments": arguments},
        )

    @pytest.mark.asyncio
    async def test_platform_frame_is_captured_mid_stream(self):
        trigger, state = self._handler_and_state()
        cred = type("C", (), {"agent_id": "agent_x"})()

        await trigger._handle_stream_event(
            self._progress({"content": PLATFORM_TEXT, PLATFORM_REPLY_TEXT_KEY: PLATFORM_TEXT}),
            state,
            cred,
            "!room:example",
        )

        assert state.platform_reply_text == PLATFORM_TEXT
        # The anti-double-send invariant: finalize fresh-sends
        # narra_reply_text to the room, so this must NOT land there.
        assert state.narra_reply_text == ""
        assert state.error_seen is False

    @pytest.mark.asyncio
    async def test_organic_narra_reply_still_captured(self):
        trigger, state = self._handler_and_state()
        cred = type("C", (), {"agent_id": "agent_x"})()

        await trigger._handle_stream_event(
            self._progress({"text": "the agent's own words"}, tool_name="narra_reply"),
            state,
            cred,
            "!room:example",
        )

        assert state.narra_reply_text == "the agent's own words"
        assert state.platform_reply_text == ""

    @pytest.mark.asyncio
    async def test_blank_platform_text_is_ignored(self):
        trigger, state = self._handler_and_state()
        cred = type("C", (), {"agent_id": "agent_x"})()

        await trigger._handle_stream_event(
            self._progress({PLATFORM_REPLY_TEXT_KEY: "   "}),
            state,
            cred,
            "!room:example",
        )

        assert state.platform_reply_text == ""
