"""
@file_name: test_im_dm_fallback_delivery_e2e.py
@author:
@date: 2026-08-06
@description: Drives the IM DM recovery slot end to end, across the two
layers that have to agree for a silent turn to reach the person waiting.

Why this exists as an integration test rather than more unit tests: the
0802 failure was never a single wrong function. Every piece was
individually fine — the model produced text, the channel had a working
send tool, the persistence layer had a "delivered" concept — and the
person on WeChat still got nothing, because nothing connected them.

Three live Telegram turns (2026-08-06) could not exercise this path at
all: once the DM protocol was fixed, the model kept replying correctly,
so the fallback never fired. It cannot be reached by hand on demand, and
the one time its wiring was broken (an empty turn envelope made it dead
code) only a database read caught it. Hence a CI-side nail.

What is asserted, in one flow:
  1. helper_llm's text is DELIVERED through the channel's registered
     sender, with the channel's addressing kwargs intact;
  2. the synthetic frame is tagged with the CHANNEL's send tool, so
  3. `chat_module` classifies the turn as an IM reply delivered to the
     origin — and NOT as an owner-facing notification;
  4. nothing is recorded when the channel refuses the send;
  5. no AgentTextDelta frames leak into the owner's chat panel.
"""
from __future__ import annotations

import pytest

# importlib, not `from ... import step_3_agent_loop`: the package re-exports
# the step FUNCTION under the same name as its module, so the plain form
# binds the function and monkeypatching module attributes fails.
import importlib

step3 = importlib.import_module(
    "narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop"
)
from narranexus.platform.channel.channel_sender_registry import ChannelSenderRegistry
from narranexus_plugins.chat_module.chat_module import ChatModule
from narranexus.platform.schema import AgentTextDelta, ProgressMessage

WECHAT_TAG = {"channel": "wechat", "room_id": "wxid_peer", "agent_id": "agent_x"}
REPLY_KWARGS = {"context_token": "tok-abc"}


@pytest.fixture
def fake_sender():
    sent: list[dict] = []
    outcome = {"success": True}

    async def _sender(agent_id, target_id, message, **kwargs):
        sent.append(
            {"agent_id": agent_id, "target_id": target_id, "message": message, **kwargs}
        )
        return outcome

    ChannelSenderRegistry.register("wechat", _sender)
    try:
        yield sent, outcome
    finally:
        ChannelSenderRegistry.unregister("wechat")


@pytest.fixture
def fake_helper(monkeypatch):
    """Stand in for the helper_llm stream. Returns a mutable list of the
    deltas it will emit, so a test can also make it produce nothing."""
    deltas = ["好的，", "这是你要的答案。"]

    async def _stream(**kwargs):
        for d in deltas:
            yield d

    monkeypatch.setattr(step3, "_generate_fallback_reply_stream", _stream)
    return deltas


async def _drive(*, cancellation=None, channel_tag=None, reply_kwargs=None):
    """Run the recovery slot in IM DM mode and collect what it yields."""
    frames = []
    async for msg in step3._stream_fallback_recovery(
        fallback_mode="no_reply_im_dm",
        captured_error=None,
        context_messages=[],
        agent_loop_response=[],
        final_output="I should tell them the answer.",
        user_input="帮我查一下",
        cancellation=cancellation,
        db=None,
        agent_id="agent_x",
        working_source="wechat",
        channel_tag=WECHAT_TAG if channel_tag is None else channel_tag,
        reply_kwargs=REPLY_KWARGS if reply_kwargs is None else reply_kwargs,
    ):
        frames.append(msg)
    return frames


@pytest.mark.asyncio
class TestSilentDmTurnReachesThePerson:
    async def test_reply_is_delivered_on_the_channel(self, fake_sender, fake_helper):
        sent, _ = fake_sender
        await _drive()

        assert len(sent) == 1, "the person must receive exactly one message"
        assert sent[0]["target_id"] == "wxid_peer"
        assert sent[0]["message"] == "好的，这是你要的答案。"
        # Without the token iLink cannot address the conversation — it has
        # to survive the trip from the builder through the turn envelope.
        assert sent[0]["context_token"] == "tok-abc"

    async def test_frame_is_tagged_as_a_channel_reply(self, fake_sender, fake_helper):
        frames = await _drive()
        progress = [f for f in frames if isinstance(f, ProgressMessage)]
        assert len(progress) == 1

        details = progress[0].details
        assert details["tool_name"] == "wechat_send"
        assert "notify_owner" not in details["tool_name"]
        assert details["reply_via"] == "helper_llm_no_reply_im_dm"

    async def test_no_text_deltas_leak_to_the_owner_panel(
        self, fake_sender, fake_helper
    ):
        """AgentTextDelta renders in the OWNER's chat window. This reply is
        addressed to the IM sender; painting it into the owner's
        conversation would fake a message the agent never sent them."""
        frames = await _drive()
        assert not [f for f in frames if isinstance(f, AgentTextDelta)]

    async def test_chat_module_reads_it_as_delivered_to_origin(
        self, fake_sender, fake_helper
    ):
        """The cross-layer assertion. step_3 emits the frame; chat_module
        independently decides what the turn was. If these two disagree the
        turn is recorded as silent even though the person got an answer —
        which is how the 0802 no-reply metric got poisoned."""
        frames = await _drive()

        assert bool(ChatModule._origin_delivered_text("wechat", frames)) is True

        im_reply, direct_notify, combined = ChatModule._split_user_visible_response(
            None, frames, "wechat"
        )
        assert im_reply == "好的，这是你要的答案。"
        # Nothing was addressed to the owner this turn.
        assert direct_notify == ""
        assert combined == im_reply


@pytest.mark.asyncio
class TestChannelAgnostic:
    """The same flow on Lark, whose extractor is shaped completely
    differently from WeChat's.

    WeChat's reads ``arguments["text"]``; Lark's parses a ``lark_cli``
    ``command`` string. A platform-written frame matches neither, so
    before ``PLATFORM_REPLY_TEXT_KEY`` existed, WeChat degraded to a
    "(sent via wechat_send)" placeholder and Lark reported the delivered
    reply as silence. Two channels with opposite failure modes is why the
    fix belongs in the handler layer, not in step_3.
    """

    async def test_lark_dm_reply_is_read_back_verbatim(self, fake_helper):
        sent: list[dict] = []

        async def _sender(agent_id, target_id, message, **kwargs):
            sent.append({"target_id": target_id, "message": message})
            return {"success": True}

        ChannelSenderRegistry.register("lark", _sender)
        try:
            frames = []
            async for msg in step3._stream_fallback_recovery(
                fallback_mode="no_reply_im_dm",
                captured_error=None,
                context_messages=[],
                agent_loop_response=[],
                final_output="",
                user_input="在吗",
                cancellation=None,
                db=None,
                agent_id="agent_x",
                working_source="lark",
                channel_tag={
                    "channel": "lark",
                    "room_id": "oc_room",
                    "agent_id": "agent_x",
                },
                reply_kwargs={},
            ):
                frames.append(msg)
        finally:
            ChannelSenderRegistry.unregister("lark")

        assert len(sent) == 1
        assert sent[0]["message"] == "好的，这是你要的答案。"

        im_reply, direct_notify, _ = ChatModule._split_user_visible_response(
            None, frames, "lark"
        )
        assert im_reply == "好的，这是你要的答案。"
        assert direct_notify == ""
        assert bool(ChatModule._origin_delivered_text("lark", frames)) is True


@pytest.mark.asyncio
class TestNothingIsRecordedWhenDeliveryFails:
    async def test_channel_refusal_records_nothing(self, fake_sender, fake_helper):
        """A refused send must leave NO synthetic frame: recording
        "replied" for a message that never left the process is the same
        class of lie as the discarded plain text this fix removes."""
        _, outcome = fake_sender
        outcome["success"] = False

        frames = await _drive()
        assert frames == []

    async def test_unregistered_channel_records_nothing(self, fake_helper):
        frames = await _drive(
            channel_tag={"channel": "nosuchchannel", "room_id": "p"},
        )
        assert frames == []

    async def test_empty_helper_output_sends_nothing(
        self, fake_sender, fake_helper, monkeypatch
    ):
        async def _empty(**kwargs):
            if False:  # pragma: no cover - shape only
                yield ""

        monkeypatch.setattr(step3, "_generate_fallback_reply_stream", _empty)
        sent, _ = fake_sender

        frames = await _drive()
        assert frames == []
        assert sent == []

    async def test_cancelled_turn_sends_nothing(self, fake_sender, fake_helper):
        """The user pressed stop. Honouring that is the whole point — and a
        cancelled turn must not have a reply pushed out on its behalf."""

        class _Cancelled:
            is_cancelled = True

        sent, _ = fake_sender
        frames = await _drive(cancellation=_Cancelled())
        assert frames == []
        assert sent == []


# ---------- 5. the no-reply sentinel ------------------------------------


@pytest.mark.asyncio
class TestNoReplySentinel:
    """The DM protocol's silence carve-out, and its robustness.

    Without an exit the platform answers even a bare "谢谢": the decision to
    run this fallback asks only whether a reply tool was called, and a model
    that correctly stayed silent called none — so the protocol's own
    exemption was unreachable in production.

    The marker is STRIPPED, not compared for equality. The helper runs on
    whichever provider the user configured (binding rule #15), so a quoted
    marker, a trailing full stop, or a prefacing sentence are all in range —
    and an equality test would deliver the literal `<<<NO_REPLY_NEEDED>>>`
    into someone's IM thread.
    """

    async def _run(self, monkeypatch, helper_output, fake_sender):
        async def _stream(**kwargs):
            yield helper_output

        monkeypatch.setattr(step3, "_generate_fallback_reply_stream", _stream)
        return await _drive(), fake_sender[0]

    @pytest.mark.parametrize(
        "helper_output",
        [
            step3.NO_REPLY_NEEDED_SENTINEL,
            f"{step3.NO_REPLY_NEEDED_SENTINEL}\n",
            f'"{step3.NO_REPLY_NEEDED_SENTINEL}"',
            f"{step3.NO_REPLY_NEEDED_SENTINEL}。",
            f"  {step3.NO_REPLY_NEEDED_SENTINEL}  ",
        ],
    )
    async def test_sentinel_variants_all_stay_silent(
        self, monkeypatch, fake_sender, helper_output
    ):
        frames, sent = await self._run(monkeypatch, helper_output, fake_sender)
        assert frames == []
        assert sent == []

    async def test_sentinel_never_reaches_the_person_even_mixed_into_prose(
        self, monkeypatch, fake_sender
    ):
        """A model that explains itself before emitting the marker must not
        leak the marker; the surviving prose is still delivered."""
        frames, sent = await self._run(
            monkeypatch,
            f"好的，我判断无需回复：{step3.NO_REPLY_NEEDED_SENTINEL}",
            fake_sender,
        )
        assert len(sent) == 1
        assert step3.NO_REPLY_NEEDED_SENTINEL not in sent[0]["message"]
        assert sent[0]["message"] == "好的，我判断无需回复："
        assert len(frames) == 1

    async def test_ordinary_reply_is_untouched(self, monkeypatch, fake_sender):
        frames, sent = await self._run(monkeypatch, "答案是 42。", fake_sender)
        assert len(sent) == 1
        assert sent[0]["message"] == "答案是 42。"
