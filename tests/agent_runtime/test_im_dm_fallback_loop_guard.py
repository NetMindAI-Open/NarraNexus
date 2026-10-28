"""
@file_name: test_im_dm_fallback_loop_guard.py
@author:
@date: 2026-08-24
@description: The DM fallback must not become a ping-pong engine.

``no_reply_im_dm`` writes a reply the agent never sent and delivers it on
the channel. Until now it asked exactly one question — "was a reply tool
called?" — and would happily invent a reply to another agent, forever.

``message_bus`` was excluded from the fallback from the start, with the
reasoning "must not answer peer agents". But agent-to-agent conversations
also arrive over IM channels: the 8/14 incident was two agents in a
NarraMessenger DM, which is precisely the path the exclusion did not
cover. These tests pin the two new gates.
"""
from __future__ import annotations

import asyncio

import pytest
from loguru import logger

from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    IM_DM_FALLBACK_BURST_LIMIT,
    _fallback_conversation_key,
    _record_fallback_delivery,
    _recent_fallback_count,
    _should_run_helper_llm_fallback,
    SKIP_REASON_AGENT_PEER,
    SKIP_REASON_FALLBACK_RATE_LIMITED,
    reset_im_dm_fallback_history,
)
from narranexus.platform.schema import ErrorMessage, ProgressMessage, ProgressStatus


import narranexus.platform.services.service_audit as audit_mod


@pytest.fixture
def log_lines():
    """Loguru does not route through `caplog`; the repo's existing sink
    fixture is the way these assertions are written elsewhere."""
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    yield lines
    logger.remove(sink_id)


def _install_fake_clock(monkeypatch, start: float) -> dict:
    """Freeze step_3's clock WITHOUT touching the stdlib `time` module.

    `step_3` does `import time`, so patching `time.monotonic` would patch
    it for the asyncio event loop too — every timeout and sleep in the
    process would read a frozen clock. Rebinding the name inside step_3's
    own namespace confines the fake to the code under test.
    """
    clock = {"t": start}

    class _FakeTime:
        @staticmethod
        def monotonic() -> float:
            return clock["t"]

    monkeypatch.setattr(_step3(), "time", _FakeTime)
    return clock


def _step3():
    """The MODULE — ``_agent_runtime_steps/__init__`` re-exports a function
    of the same name, so a plain ``from ... import step_3_agent_loop`` hands
    back the function."""
    import importlib

    return importlib.import_module(
        "narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop"
    )


def _idle_progress() -> ProgressMessage:
    """A tool call that is NOT a reply — the shape that arms the fallback."""
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="get_chat_history",
        status=ProgressStatus.COMPLETED,
        details={"tool_name": "mcp__chat_module__get_chat_history"},
    )


def _decide(**overrides):
    kwargs = dict(
        working_source="narramessenger",
        agent_loop_response=[_idle_progress()],
        cancellation=None,
        is_direct_message=True,
    )
    kwargs.update(overrides)
    return _should_run_helper_llm_fallback(**kwargs)


# ── The baseline this PR must not break ───────────────────────────────

def test_human_dm_still_gets_its_fallback():
    mode, reason = _decide()
    assert mode == "no_reply_im_dm"
    assert reason == ""


# ── Gate 1: never invent a reply to another agent ─────────────────────

def test_agent_peer_dm_gets_no_invented_reply():
    mode, reason = _decide(is_agent_peer=True)
    assert mode is None
    assert reason == "agent_peer_no_fallback"


def test_agent_peer_gate_does_not_leak_into_group_rooms():
    """Group rooms were already silent for a different reason; the more
    specific existing reason must survive."""
    mode, reason = _decide(is_direct_message=False, is_agent_peer=True)
    assert mode is None
    assert reason == "group_room_may_stay_silent"


def test_organic_reply_still_reported_as_such_for_an_agent_peer():
    """Reason precedence: what actually happened beats what we'd have
    refused to do anyway."""
    reply = ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="narra_reply",
        status=ProgressStatus.COMPLETED,
        details={"tool_name": "mcp__narramessenger_module__narra_reply",
                 "arguments": {"content": "hi"}},
    )
    mode, reason = _decide(agent_loop_response=[reply], is_agent_peer=True)
    assert mode is None
    assert reason == "already_replied_via_tool"


def test_fatal_error_reason_beats_agent_peer_reason():
    fatal = ErrorMessage(
        error_message="boom", error_type="RuntimeError", severity="fatal"
    )
    mode, reason = _decide(
        agent_loop_response=[_idle_progress(), fatal], is_agent_peer=True
    )
    assert mode is None
    assert reason == "fatal_no_invented_reply"


# ── Gate 2: a steady stream of fallbacks is a loop being fed ──────────

def test_fallback_arms_below_the_burst_limit():
    mode, _ = _decide(recent_fallback_count=IM_DM_FALLBACK_BURST_LIMIT - 1)
    assert mode == "no_reply_im_dm"


def test_fallback_stops_at_the_burst_limit():
    mode, reason = _decide(recent_fallback_count=IM_DM_FALLBACK_BURST_LIMIT)
    assert mode is None
    assert reason == "fallback_rate_limited"


# ── The counter itself ────────────────────────────────────────────────

def test_counter_is_scoped_per_conversation():
    a = _fallback_conversation_key({"channel": "narramessenger", "room_id": "!a"}, "agt_1")
    b = _fallback_conversation_key({"channel": "narramessenger", "room_id": "!b"}, "agt_1")
    assert a != b

    _record_fallback_delivery(a)
    _record_fallback_delivery(a)
    assert _recent_fallback_count(a) == 2
    assert _recent_fallback_count(b) == 0, "one noisy room must not gag another"


def test_counter_ignores_an_unidentifiable_conversation():
    """No room → no key.

    (The predicate used to be "no channel AND no room". ``channel`` is
    always set — it is the trigger's own name — so that made the empty-room
    case fall through to a per-CHANNEL bucket where unrelated DMs would
    starve each other's budget, which is the opposite of what this carve-out
    is for.)"""
    key = _fallback_conversation_key({}, "agt_1")
    assert key == ""
    _record_fallback_delivery(key)
    assert _recent_fallback_count(key) == 0


def test_same_room_on_two_channels_is_two_conversations():
    a = _fallback_conversation_key({"channel": "telegram", "room_id": "123"}, "agt_1")
    b = _fallback_conversation_key({"channel": "slack", "room_id": "123"}, "agt_1")
    assert a != b


def test_two_agents_in_one_room_get_separate_budgets():
    """The key is agent-scoped. Today the gate only fires on DMs (one of
    our agents alone in the room), so this cannot bite yet — but the map
    is module-level and shared by every agent in the runtime process, so
    the moment the gate widens beyond DMs an agent-blind key would let
    agent A's three fallbacks silently gag agent B."""
    a = _fallback_conversation_key({"channel": "slack", "room_id": "C1"}, "agt_a")
    b = _fallback_conversation_key({"channel": "slack", "room_id": "C1"}, "agt_b")
    assert a != b

    _record_fallback_delivery(a)
    _record_fallback_delivery(a)
    _record_fallback_delivery(a)
    assert _recent_fallback_count(b) == 0


# ── Memory ────────────────────────────────────────────────────────────
def test_history_does_not_grow_without_bound():
    """`_recent_fallback_count` only cleans the key it is asked about, so
    a room that gets one fallback and never another would keep its entry
    for the life of the process."""
    step3 = _step3()

    reset_im_dm_fallback_history()
    for i in range(500):
        _record_fallback_delivery(f"telegram:room{i}")

    # Everything is inside the window, so nothing is droppable yet.
    assert len(step3._im_dm_fallback_history) == 500

    # Age them all out, then record one more.
    for stamps in step3._im_dm_fallback_history.values():
        stamps[:] = [t - step3.IM_DM_FALLBACK_WINDOW_SECONDS - 1 for t in stamps]
    _record_fallback_delivery("telegram:fresh")

    assert len(step3._im_dm_fallback_history) == 1
    assert "telegram:fresh" in step3._im_dm_fallback_history
    reset_im_dm_fallback_history()


def test_pruning_keeps_a_conversation_that_is_still_inside_its_window():
    step3 = _step3()

    reset_im_dm_fallback_history()
    _record_fallback_delivery("telegram:live")
    _record_fallback_delivery("telegram:other")
    assert _recent_fallback_count("telegram:live") == 1
    assert len(step3._im_dm_fallback_history) == 2
    reset_im_dm_fallback_history()


# ─────────────────────────────────────────────────────────────────────
# The chain this PR closes
#
# #359 added a "Breaking a Loop" section telling the agent it may stay
# silent, and left a warning in the source saying it did not take effect:
# staying silent means not calling the reply tool, which is precisely what
# `no_reply_im_dm` treats as "forgot to reply" — so the platform wrote one
# anyway. #362 gave the platform the `is_agent_peer` signal. This PR is the
# gate that turns chosen silence into actual silence.
#
# Pinned end-to-end because each half is individually green while the chain
# is broken: the prompt says one thing, the runtime does another, and
# nothing fails.
# ─────────────────────────────────────────────────────────────────────


def test_the_prompt_permits_silence_and_the_runtime_now_honours_it():
    """Prompt half and runtime half must agree for an agent peer."""
    from narranexus.platform.channel.channel_prompts import (
        COMMUNICATION_PROTOCOL_DIRECT,
    )
    from narranexus.platform.schema.channel_tag import AGENT_PEER_MARKER

    # The prompt tells the model it may stay silent toward a machine...
    assert "### Breaking a Loop" in COMMUNICATION_PROTOCOL_DIRECT
    assert AGENT_PEER_MARKER in COMMUNICATION_PROTOCOL_DIRECT

    # ...and the runtime no longer overrides that choice.
    mode, reason = _decide(is_agent_peer=True)
    assert mode is None
    assert reason == "agent_peer_no_fallback"


def test_a_human_dm_keeps_the_0802_fix():
    """The other half of the contract, and the reason this gate is narrow.

    ``no_reply_im_dm`` IS the 0802 fix (a person sent "hello" and got
    nothing). Suppressing it for humans to make the loop-breaker "work
    everywhere" would reopen that. A human DM still gets a written reply —
    the burst limit bounds a loop instead of stopping it dead.
    """
    mode, _ = _decide(is_agent_peer=False)
    assert mode == "no_reply_im_dm"

    for n in range(IM_DM_FALLBACK_BURST_LIMIT):
        assert _decide(recent_fallback_count=n)[0] == "no_reply_im_dm"
    assert _decide(recent_fallback_count=IM_DM_FALLBACK_BURST_LIMIT)[0] is None


def test_the_source_no_longer_claims_the_section_is_inert():
    """#359 left `THIS SECTION DOES NOT TAKE EFFECT YET` with an explicit
    instruction to delete it in the commit that adds the gate. A stale
    warning is as misleading as a stale promise."""
    import inspect

    from narranexus.platform.channel import channel_prompts

    from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
        SKIP_REASON_AGENT_PEER,
        SKIP_REASON_FALLBACK_RATE_LIMITED,
    )

    src = inspect.getsource(channel_prompts)
    assert "DOES NOT TAKE EFFECT YET" not in src
    # Reference the CONSTANTS, not copies of their values: asserting the
    # literals would stay green after a rename, because the stale comment
    # still contains the old word — which is the drift this claims to catch.
    assert SKIP_REASON_AGENT_PEER in src
    assert SKIP_REASON_FALLBACK_RATE_LIMITED in src


# ── The envelope → decision hop ───────────────────────────────────────
#
# Every test above calls `_should_run_helper_llm_fallback` with
# `is_agent_peer=True` directly, so none of them checks that the key name
# on the wire matches the one read here. This file's own comments record
# TWO incidents that died exactly on this hop — `ctx` passed where
# `context` was meant (the whole IM DM fallback was dead code), and the
# envelope added to one of four construction sites. Both were all-green.


def test_the_flag_survives_the_envelope_hop():
    from types import SimpleNamespace

    from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
        _channel_turn_envelope,
    )
    from narranexus.platform.channel.channel_prompts import ROOM_TYPE_DIRECT
    from narranexus.platform.schema.channel_tag import ChannelTag

    # Built through ChannelTag.to_dict() on purpose — hand-writing the dict
    # would let a key rename drift both sides together and pin nothing.
    tag = ChannelTag(
        channel="narramessenger", sender_name="Liam", sender_id="@agent-x:h",
        room_id="!r", is_agent_peer=True,
    )
    context = SimpleNamespace(
        ctx_data=SimpleNamespace(
            extra_data={
                "channel_tag": tag.to_dict(),
                "channel_room_type": ROOM_TYPE_DIRECT,
            }
        )
    )

    envelope = _channel_turn_envelope(context)
    mode, reason = _should_run_helper_llm_fallback(
        working_source="narramessenger",
        agent_loop_response=[_idle_progress()],
        cancellation=None,
        is_direct_message=(
            envelope.get("channel_room_type") == ROOM_TYPE_DIRECT
        ),
        is_agent_peer=bool(
            (envelope.get("channel_tag") or {}).get("is_agent_peer", False)
        ),
    )
    assert mode is None
    assert reason == "agent_peer_no_fallback"


def test_a_conversation_without_a_room_is_not_counted():
    """`channel` is always set (it is the trigger's own name), so the
    empty-key carve-out hinges on `room_id` alone. Without this, an empty
    room falls into a per-CHANNEL bucket where unrelated DMs starve each
    other's budget."""
    assert _fallback_conversation_key({"channel": "telegram"}, "agt_1") == ""
    assert _fallback_conversation_key({}, "agt_1") == ""
    assert _fallback_conversation_key(
        {"channel": "telegram", "room_id": "r1"}, "agt_1"
    ) == "agt_1:telegram:r1"


def test_the_key_is_normalised_inside_the_function():
    """Decision side and record side compute this key independently; if one
    normalised `agent_id` and the other did not, they would address two
    buckets that never meet and the gate would silently never fire."""
    assert _fallback_conversation_key({"channel": "c", "room_id": "r"}, None) == \
        _fallback_conversation_key({"channel": "c", "room_id": "r"}, "")


# ── The audit row ─────────────────────────────────────────────────────
#
# `_audit_fallback_suppressed` is the only reason this gate is diagnosable
# after the fact: `fallback_rate_limited` fires off an in-process window a
# restart wipes, and the person on the other end just sees the assistant
# stop answering. If the write never happens, "the audit never ran" and
# "the audit ran and the table is empty" look identical afterwards — the
# failure is invisible precisely when it is needed.


async def test_a_suppressed_turn_writes_one_audit_row(monkeypatch):
    rows = []

    class _Auditor:
        def __init__(self, service):
            self.service = service

        async def event(self, event_type, detail=None):
            rows.append((self.service, event_type, detail))
            return True  # the real one returns whether the row landed

    # Patch the CLASS the module imports lazily, and clear the cached
    # instance so the patched one is used. Letting the real ServiceAuditor
    # run would `await get_db_client()`, fail, and get swallowed by the
    # never-raise handler — the assertion would pass having tested nothing.
    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)

    await _step3()._audit_fallback_suppressed(
        reason=SKIP_REASON_AGENT_PEER,
        agent_id="agt_1",
        channel_tag={"channel": "narramessenger", "room_id": "!r",
                     "is_agent_peer": True},
        window_count=7,
        conversation_key="agt_1:narramessenger:!r",
    )

    assert len(rows) == 1
    service, event_type, detail = rows[0]
    assert service == _step3()._FALLBACK_AUDIT_SERVICE
    assert event_type == SKIP_REASON_AGENT_PEER
    assert {
        "agent_id",
        "channel",
        "room_id",
        "is_agent_peer",
        "window_count",
        "suppressed_since_last_row",
    } <= set(detail), (
        "a field the owner reads to answer 'why did it go quiet' was "
        f"dropped from the payload: {sorted(detail)}"
    )
    assert detail["window_count"] == 7
    assert detail["room_id"] == "!r"


async def test_the_audit_is_cooled_per_conversation_and_reason(monkeypatch):
    """The write rate is set by the far side, not by us."""
    rows = []

    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            rows.append((event_type, detail))
            return True

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)

    async def _fire(reason, key):
        await _step3()._audit_fallback_suppressed(
            reason=reason, agent_id="a", channel_tag={"channel": "c", "room_id": "r"},
            window_count=1, conversation_key=key,
        )

    for _ in range(5):
        await _fire(SKIP_REASON_AGENT_PEER, "a:c:r1")
    assert len(rows) == 1, "a fed conversation must not write a row per turn"

    await _fire(SKIP_REASON_AGENT_PEER, "a:c:r2")
    assert len(rows) == 2, "a different conversation has its own slot"

    await _fire(SKIP_REASON_FALLBACK_RATE_LIMITED, "a:c:r1")
    assert len(rows) == 3, (
        "the two gates are different facts — one must not eat the other's slot"
    )


async def test_a_failed_audit_write_does_not_arm_the_cooldown(monkeypatch):
    """A transient DB blip must not silence this conversation for the rest
    of the window.

    The first version of this test made its fake RAISE — which the real
    ``ServiceAuditor.event`` never does (``_emit`` swallows). So it pinned
    a property only the fake had, while production armed the cooldown on
    every failed write. ``event`` now reports the outcome, and the fake
    reports it the same way.
    """
    outcomes = [False, True]
    calls = []

    class _FailsThenWorks:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            calls.append(detail)
            return outcomes[len(calls) - 1]

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _FailsThenWorks)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)

    for _ in range(2):
        await _step3()._audit_fallback_suppressed(
            reason=SKIP_REASON_AGENT_PEER, agent_id="a",
            channel_tag={"channel": "c", "room_id": "r"},
            window_count=1, conversation_key="a:c:r",
        )
    assert len(calls) == 2, "the first (failed) write must not arm the cooldown"
    assert calls[1]["suppressed_since_last_row"] == 2, (
        "a failed write must not drop the window's suppression count"
    )


async def test_the_real_auditor_reports_its_outcome(monkeypatch):
    """The contract the fakes above imitate, checked on the real class.

    Asserting the ``-> bool`` ANNOTATION (the first version) proved
    nothing: dropping the ``return True`` while leaving the annotation in
    place kept it green, and that is exactly the production bug this whole
    round is about.

    The fakes here must return what the REAL repository returns. An earlier
    version had ``record()`` return ``None`` — harmless while ``event()``
    ignored it, and silently wrong the moment ``event()` started
    propagating it. Chain-level coverage against the real repository lives
    in ``tests/services/test_service_audit_write_outcome.py``.
    """
    from narranexus.platform.services.service_audit import ServiceAuditor

    class _OkRepo:
        async def record(self, *a, **k):
            return True

    class _BoomRepo:
        async def record(self, *a, **k):
            raise RuntimeError("db down")

    auditor = ServiceAuditor("test_plane")

    async def _ok(self):
        return _OkRepo()

    async def _boom(self):
        return _BoomRepo()

    monkeypatch.setattr(type(auditor), "_get_repo", _ok)
    assert await auditor.event("e", {"k": 1}) is True

    monkeypatch.setattr(type(auditor), "_get_repo", _boom)
    # Still never raises — that contract is unchanged — but now it says so.
    assert await auditor.event("e", {"k": 1}) is False


async def test_each_audit_row_reports_only_what_happened_since_the_last(
    monkeypatch,
):
    """`suppressed_since_last_row` must reset on a landed write.

    Without the reset it becomes a lifetime total, and "how hard was this
    conversation being suppressed in the last window" — the question these
    rows exist to answer — silently turns into "how hard has it ever been".
    """
    rows = []

    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            rows.append(detail["suppressed_since_last_row"])
            return True

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)

    # A fake clock rather than a zero cooldown. Zeroing the cooldown ALSO
    # makes every entry immediately stale, so the pruner wipes the counter
    # and the reset-on-write behaviour becomes unobservable — the setup
    # would hide the very thing under test.
    clock = _install_fake_clock(monkeypatch, 1000.0)
    cooldown = _step3()._FALLBACK_AUDIT_COOLDOWN_SECONDS

    async def _suppress():
        await _step3()._audit_fallback_suppressed(
            reason=SKIP_REASON_AGENT_PEER, agent_id="a",
            channel_tag={"channel": "c", "room_id": "r"},
            window_count=0, conversation_key="a:c:r",
        )

    # The first suppression writes straight away — a conversation going
    # quiet should show up immediately, not one cooldown later.
    await _suppress()
    assert rows == [1]

    # Two more inside the cooldown: no rows, but they are counted.
    await _suppress()
    await _suppress()
    assert rows == [1]

    # Past the cooldown, the next one writes and reports the three
    # suppressions since the last row — not the five since the start.
    clock["t"] += cooldown + 1
    await _suppress()
    assert rows == [1, 3], f"counter is accumulating across rows: {rows}"


async def test_only_the_two_new_reasons_are_audited(monkeypatch):
    """The other three skips are recomputable from the turn itself, so
    auditing them would be noise. Pinned as BEHAVIOUR, not by reading the
    generator's source: "let's just audit all five" is an easy and wrong
    cleanup, and a source-string assertion cannot tell whether the branch
    is reachable.
    """
    written = []

    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            written.append(event_type)
            return True

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)

    audited, skipped = [], []
    for i, reason in enumerate([
        SKIP_REASON_AGENT_PEER,
        SKIP_REASON_FALLBACK_RATE_LIMITED,
        "already_replied_via_tool",
        "group_room_may_stay_silent",
        "fatal_no_invented_reply",
        "non_chat_trigger",
    ]):
        did = await _step3()._maybe_audit_fallback_skip(
            skip_reason=reason, agent_id="a",
            channel_tag={"channel": "c", "room_id": f"r{i}"},
            window_count=0, conversation_key=f"a:c:r{i}",
        )
        (audited if did else skipped).append(reason)

    assert audited == [SKIP_REASON_AGENT_PEER, SKIP_REASON_FALLBACK_RATE_LIMITED]
    assert len(written) == 2
    assert "already_replied_via_tool" in skipped


def test_the_generator_dispatches_through_the_whitelist_helper():
    """The one hop the tests above cannot reach — the call itself, deep in
    the generator's skip branch. This file's comments record two incidents
    that died exactly on this kind of hop, both all-green, so the residual
    is named rather than left implicit.
    """
    import inspect

    src = inspect.getsource(_step3().step_3_agent_loop)
    assert "_maybe_audit_fallback_skip(" in src


async def test_both_audit_maps_are_reclaimed_when_a_conversation_goes_quiet(
    monkeypatch,
):
    """Neither audit map may grow without bound in a days-long process.

    Both are keyed by `room_id`, which the far side supplies, and gate 1
    fires on EVERY turn of an A2A conversation — no error needed. Binding
    rule #14 makes "runs for days" the normal case, so an unbounded
    per-room map is a leak, not a theoretical one.
    """
    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            rows.append(detail)
            return True

    rows = []
    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)
    clock = _install_fake_clock(monkeypatch, 1000.0)

    # Twice per room: the first lands a row and arms the cooldown, the
    # second is cooled away and leaves a pending turn behind. A single
    # suppression per room would settle its own debt on the spot and this
    # test would pass without the settle-up path existing at all.
    for i in range(40):
        for _ in range(2):
            await _step3()._audit_fallback_suppressed(
                reason=SKIP_REASON_AGENT_PEER,
                agent_id="a",
                channel_tag={"channel": "c", "room_id": f"room-{i}"},
                window_count=0,
                conversation_key=f"a:c:room-{i}",
            )

    assert len(_step3()._fallback_audit_cooldown) == 40
    assert len(_step3()._fallback_suppressed_since_row) == 40

    # Every conversation goes quiet. One more turn, from a fresh room, is
    # the only thing that runs the sweep — it must reclaim both maps.
    clock["t"] += _step3()._FALLBACK_SUPPRESSED_RETENTION_SECONDS + 1
    await _step3()._audit_fallback_suppressed(
        reason=SKIP_REASON_AGENT_PEER,
        agent_id="a",
        channel_tag={"channel": "c", "room_id": "later"},
        window_count=0,
        conversation_key="a:c:later",
    )

    assert list(_step3()._fallback_audit_cooldown) == ["agent_peer_no_fallback:a:c:later"]
    # Reclaiming must SETTLE the debt, not discard it. Asserting the row
    # count is what stops the settle-up path from being a no-op that this
    # test would otherwise pass straight through.
    #
    # One sweep writes at most `_FALLBACK_SETTLE_BATCH` closing rows; the
    # rest stay in the map, unpopped, and drain on later sweeps. So the
    # first sweep is capped and the total is only reached by draining.
    cap = _step3()._FALLBACK_SETTLE_BATCH
    assert cap < 40, "this test needs the cap to actually bite"
    settled = [r for r in rows if r["final_tally"]]
    assert len(settled) == cap, f"the settle-up batch is unbounded: {len(settled)}"
    assert len(_step3()._fallback_suppressed_since_row) == 40 - cap + 1

    # Drained WITHOUT advancing the clock, and in a bounded number of
    # sweeps. The over-cap entries are already past their retention window,
    # so the very next sweep must take them. Popping them and putting them
    # back would refresh `touched` and renew that window — and a drain loop
    # that advanced the clock would hide exactly that, since the renewed
    # entries would simply expire a second time.
    sweeps = 0
    while len([r for r in rows if r["final_tally"]]) < 40:
        sweeps += 1
        assert sweeps <= 40 // cap + 1, (
            "over-cap entries are not draining on the next sweep — their "
            "retention window is being renewed"
        )
        await _step3()._audit_fallback_suppressed(
            reason=SKIP_REASON_AGENT_PEER,
            agent_id="a",
            channel_tag={"channel": "c", "room_id": "drain"},
            window_count=0,
            conversation_key="a:c:drain",
        )

    settled = [r for r in rows if r["final_tally"]]
    assert {r["room_id"] for r in settled} == {f"room-{i}" for i in range(40)}
    assert all(r["suppressed_since_last_row"] == 1 for r in settled)


async def test_the_counter_is_reclaimed_even_when_the_audit_write_never_lands(
    monkeypatch,
):
    """The failure path increments too, so it must be swept too.

    A DB outage is the condition under which these maps grow fastest: no
    write lands, so no cooldown ever arms, so every single turn increments
    the counter. Hanging the sweep off a successful write — which is where
    it started this round — means the one situation that needs reclaiming
    is the one where reclaiming never runs.
    """
    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            attempts.append(detail)
            return False  # never lands; ServiceAuditor swallows the cause

    attempts = []
    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)
    clock = _install_fake_clock(monkeypatch, 1000.0)

    for i in range(30):
        await _step3()._audit_fallback_suppressed(
            reason=SKIP_REASON_AGENT_PEER,
            agent_id="a",
            channel_tag={"channel": "c", "room_id": f"room-{i}"},
            window_count=0,
            conversation_key=f"a:c:room-{i}",
        )

    assert not _step3()._fallback_audit_cooldown, "a failed write must not arm cooling"
    assert len(_step3()._fallback_suppressed_since_row) == 30

    clock["t"] += _step3()._FALLBACK_SUPPRESSED_RETENTION_SECONDS + 1
    await _step3()._audit_fallback_suppressed(
        reason=SKIP_REASON_AGENT_PEER,
        agent_id="a",
        channel_tag={"channel": "c", "room_id": "later"},
        window_count=0,
        conversation_key="a:c:later",
    )
    # The settle-up was attempted and also failed. It must NOT put the
    # number back: nothing would ever trigger a retry (the conversation has
    # been silent for the whole retention window), so a re-added entry
    # could only be settled by another settle-up — pending forever, swept
    # forever, and the map is unbounded again.
    assert [r for r in attempts if r.get("final_tally")], "settle-up never attempted"
    assert list(_step3()._fallback_suppressed_since_row) == [
        "agent_peer_no_fallback:a:c:later"
    ]


async def test_an_unidentifiable_conversation_still_gets_a_row_every_time(
    monkeypatch,
):
    """No conversation key → no cooling, and no state left behind.

    Cooling these would put every unidentifiable turn in one shared slot,
    where the first would mute the audit for all the others. That shared
    bucket is exactly the defect the rate-limit key was fixed for, so it
    must not be reintroduced on the audit plane.
    """
    rows = []

    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            rows.append(detail)
            return True

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)
    _install_fake_clock(monkeypatch, 1000.0)

    for _ in range(3):
        await _step3()._audit_fallback_suppressed(
            reason=SKIP_REASON_AGENT_PEER,
            agent_id="a",
            channel_tag={"channel": "c", "room_id": ""},
            window_count=0,
            conversation_key="",
        )

    assert len(rows) == 3, "an unidentifiable turn must never be cooled away"
    # One caliber on every row: "how many suppressed turns does this row
    # account for". Nothing is cooled here, so each row accounts for
    # exactly its own turn. Writing 0 would make the field structurally
    # constant on this class of rows — the defect `window_count` was
    # called out for, reappearing under a different name.
    assert [r["suppressed_since_last_row"] for r in rows] == [1, 1, 1]
    assert not _step3()._fallback_audit_cooldown
    assert not _step3()._fallback_suppressed_since_row


async def test_a_conversation_that_ends_inside_its_cooldown_still_reports_every_turn(
    monkeypatch,
):
    """The whole point of this field is re-deriving the threshold from data.

    A 12-turn A2A exchange that finishes inside one cooldown window is the
    COMMON shape, not an edge case: gate 1 fires on every turn of an A2A
    conversation with no error condition needed. Turn 1 lands a row; turns
    2-12 are cooled away; the conversation then ends. With no settle-up,
    `SUM(suppressed_since_last_row)` reports 1 where the truth is 12, and
    the shortfall grows with how short conversations are — so the metric
    is worst exactly where the traffic is.
    """
    rows = []

    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            rows.append(detail)
            return True

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)
    clock = _install_fake_clock(monkeypatch, 1000.0)

    for _ in range(12):
        await _step3()._audit_fallback_suppressed(
            reason=SKIP_REASON_AGENT_PEER,
            agent_id="a",
            channel_tag={"channel": "c", "room_id": "r"},
            window_count=0,
            conversation_key="a:c:r",
        )
        clock["t"] += 4.0  # the 8/14 cadence, ~1 message per 4 seconds

    assert len(rows) == 1, "the cooldown should have collapsed 12 turns into 1 row"

    # The conversation ends. Some other conversation's turn is what runs
    # the sweep — nothing here polls.
    clock["t"] += _step3()._FALLBACK_SUPPRESSED_RETENTION_SECONDS + 1
    await _step3()._audit_fallback_suppressed(
        reason=SKIP_REASON_AGENT_PEER,
        agent_id="a",
        channel_tag={"channel": "c", "room_id": "elsewhere"},
        window_count=0,
        conversation_key="a:c:elsewhere",
    )

    for_r = [r for r in rows if r["room_id"] == "r"]
    assert sum(r["suppressed_since_last_row"] for r in for_r) == 12, (
        f"suppressed turns went missing: {for_r}"
    )
    # Closing rows must be distinguishable, or counting rows over-reports
    # each ended conversation by one.
    assert [r["final_tally"] for r in for_r] == [False, True]


async def test_two_interleaved_turns_do_not_report_the_same_suppression_twice(
    monkeypatch,
):
    """The read-modify-write straddles the audit `await`.

    The cooldown is armed only after the write lands, so two turns of the
    same conversation that interleave across that await both see an unarmed
    slot and both write a row. Debiting each row by what it carried keeps
    the total honest; resetting the counter to 0 would let the second row
    re-report the turn the first one already carried.
    """
    rows = []
    gate = asyncio.Event()

    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            rows.append(detail)
            await gate.wait()  # hold both writers inside the await
            return True

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)
    _install_fake_clock(monkeypatch, 1000.0)

    async def _turn():
        await _step3()._audit_fallback_suppressed(
            reason=SKIP_REASON_AGENT_PEER,
            agent_id="a",
            channel_tag={"channel": "c", "room_id": "r"},
            window_count=0,
            conversation_key="a:c:r",
        )

    task_a = asyncio.create_task(_turn())
    task_b = asyncio.create_task(_turn())
    await asyncio.sleep(0)  # both reach the await
    gate.set()
    await asyncio.gather(task_a, task_b)

    assert len(rows) == 2, "both turns raced past an unarmed cooldown"
    assert sum(r["suppressed_since_last_row"] for r in rows) == 2, (
        f"two turns accounted for more than two suppressions: {rows}"
    )
    pending = _step3()._fallback_suppressed_since_row["agent_peer_no_fallback:a:c:r"]
    assert pending.count == 0, (
        f"the remainder must be self-consistent after both debits: {pending}"
    )


async def test_a_cooled_turn_still_settles_other_conversations_debts(monkeypatch):
    """Settle-up must not sit behind the cooldown early-return.

    The reclaimed entries were popped by the sweep in this same call, so a
    path that skips the settle-up does not defer them — it drops them. And
    a cooled turn is the COMMON case on a busy agent: gate 1 fires on every
    turn of an A2A conversation, so most turns find their own cooldown
    armed and return early.

    Without this, moving the settle-up below that `return` keeps the whole
    suite green while the closing rows silently stop being written — the
    "chain quietly unhooked, all tests still pass" shape this file has
    already recorded twice.
    """
    rows = []

    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            rows.append(detail)
            return True

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)
    clock = _install_fake_clock(monkeypatch, 1000.0)
    cooldown = _step3()._FALLBACK_AUDIT_COOLDOWN_SECONDS
    retention = _step3()._FALLBACK_SUPPRESSED_RETENTION_SECONDS

    async def _suppress(room):
        await _step3()._audit_fallback_suppressed(
            reason=SKIP_REASON_AGENT_PEER,
            agent_id="a",
            channel_tag={"channel": "c", "room_id": room},
            window_count=0,
            conversation_key=f"a:c:{room}",
        )

    # Y leaves a debt behind: first turn lands a row and arms its cooldown,
    # second turn is cooled away and accumulates.
    await _suppress("Y")
    await _suppress("Y")

    # X starts late enough that Y is still inside its retention window here
    # (so X's own turn does not settle Y), but early enough that X's
    # cooldown is still armed at the moment of the turn under test.
    clock["t"] = 1000.0 + retention - 100.0
    await _suppress("X")
    x_armed_at = clock["t"]
    assert not [r for r in rows if r["final_tally"]], "Y settled too early"

    # The turn under test: Y is now past retention, X is still cooled.
    clock["t"] = 1000.0 + retention + 50.0
    assert clock["t"] - x_armed_at < cooldown, "X must still be cooled"
    before = len(rows)
    await _suppress("X")

    assert len(rows) == before + 1, (
        "the cooled turn should have written nothing of its own, only Y's "
        f"closing row: {rows[before:]}"
    )
    settled = [r for r in rows if r["final_tally"]]
    assert [r["room_id"] for r in settled] == ["Y"]
    assert settled[0]["suppressed_since_last_row"] == 1


async def test_a_dropped_closing_row_says_how_much_was_lost(monkeypatch, log_lines):
    """A failed closing write is unrecoverable, so it must leave the number.

    The entry is already out of the map and is deliberately not put back,
    so nothing will ever retry it. The repository logs its own failure but
    does not carry `suppressed_since_last_row`, which makes this warning
    the only trace of how much went missing.

    `event()` never raises, so a settle-up that ignored the return value
    would take the silent path here while its docstring claimed otherwise.
    """
    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            return False

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)
    clock = _install_fake_clock(monkeypatch, 1000.0)

    _step3()._fallback_suppressed_since_row["agent_peer_no_fallback:a:c:r"] = (
        _step3()._PendingTally(7, 1000.0, SKIP_REASON_AGENT_PEER, "a", "c", "r")
    )
    clock["t"] += _step3()._FALLBACK_SUPPRESSED_RETENTION_SECONDS + 1

    await _step3()._audit_fallback_suppressed(
        reason=SKIP_REASON_AGENT_PEER,
        agent_id="a",
        channel_tag={"channel": "c", "room_id": "other"},
        window_count=0,
        conversation_key="a:c:other",
    )

    dropped = [ln for ln in log_lines if "settle-up row dropped" in ln]
    assert dropped, f"a lost tally left no trace: {log_lines}"
    assert "tally=7" in dropped[0], f"the number itself was not logged: {dropped[0]}"
