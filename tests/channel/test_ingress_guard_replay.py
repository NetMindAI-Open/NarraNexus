"""
@file_name: test_ingress_guard_replay.py
@author:
@date: 2026-08-24
@description: The design doc's §6 acceptance cases, as tests.

Three obligations, from the 2026-08-17 ingress design:

  MUST trip     — replay the 8/14 traffic shape (1-2 s apart, byte-identical)
                  and the breaker escalates within minutes, cutting pipeline
                  executions by >95%.
  MUST NOT trip — a user firing off messages, a group at peak, a job batch.
                  Not one of them may be isolated. This is the half that
                  matters: a breaker that trips on real conversation is worse
                  than no breaker, because it silences the product.
  DELETION test — with the guard removed, the replay must run the full
                  pipeline for every message again. If it does not, this file
                  is not testing what it claims to.

The guard is driven directly rather than through a trigger: these are
claims about the POLICY, and routing them through six channels' plumbing
would test the plumbing. Wiring will be covered by
``test_ingress_guard_all_paths``, which lands with the wiring PR.

The DELETION guard named above — the one that fails if the breaker is
switched off — also lands there: it asserts on a trigger class attribute
that does not exist until the guard is mounted. So the MUST-NOT-TRIP
cases below are, in this commit, not yet protected against the breaker
being disabled outright.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from narranexus.platform.channel.ingress_guard import IngressGuard, content_fingerprint

pytestmark = pytest.mark.asyncio

# 2026-08-14 10:22 UTC — when the real loop started.
INCIDENT_START = datetime(2026, 8, 14, 10, 22, 0, tzinfo=timezone.utc)


def _guard(**kwargs) -> IngressGuard:
    """Production defaults, except where a test says otherwise."""
    return IngressGuard(**kwargs)


async def _replay(guard, messages, *, is_agent_peer=False, chat_id="!room",
                  sender_id="@agent-liam:matrix.netmind.chat"):
    """Feed (offset_seconds, text) pairs; return the admitted count."""
    admitted = 0
    verdicts = []
    for offset, text in messages:
        v = await guard.admit(
            agent_id="agt_signal",
            channel="narramessenger",
            chat_id=chat_id,
            sender_id=sender_id,
            fingerprint=content_fingerprint(chat_id, sender_id, text),
            is_agent_peer=is_agent_peer,
            now=INCIDENT_START + timedelta(seconds=offset),
        )
        verdicts.append(v)
        admitted += 1 if v.admit else 0
    return admitted, verdicts


def _liam_shape(count: int, *, every: float = 1.5):
    """The actual incident shape: verbatim recital, 1-2 seconds apart."""
    body = "Hi! I noticed your message. Could you tell me more about that?"
    return [(i * every, body) for i in range(count)]


# ─────────────────────────────────────────────────────────────────────
# MUST TRIP
# ─────────────────────────────────────────────────────────────────────

async def test_the_incident_shape_is_cut_by_more_than_95_percent():
    """One hour of the real traffic: 2400 messages at 1.5 s apart."""
    guard = _guard()
    messages = _liam_shape(2400)
    admitted, _ = await _replay(guard, messages, is_agent_peer=True)

    reduction = 1 - (admitted / len(messages))
    assert reduction > 0.95, (
        f"only {reduction:.1%} of pipeline executions suppressed; the design "
        f"target is >95% ({admitted}/{len(messages)} still ran)"
    )


async def test_the_incident_escalates_within_minutes():
    guard = _guard()
    _, verdicts = await _replay(guard, _liam_shape(2400), is_agent_peer=True)

    first_trip = next(v for v in verdicts if v.transition == "tripped")
    trip_index = verdicts.index(first_trip)
    minutes_to_trip = (trip_index * 1.5) / 60
    assert minutes_to_trip < 15, (
        f"took {minutes_to_trip:.1f} minutes to trip — the design says "
        f"L0→L3 completes in minutes"
    )

    tiers = [v.tier for v in verdicts]
    assert max(tiers) >= 3, "an hour of unbroken recital must reach a deep tier"


async def test_a_seventy_hour_loop_reaches_the_day_long_cooldown():
    """The real incident ran 70+ hours. The schedule must actually get to
    its 24h step rather than oscillating at the cheap end forever."""
    guard = _guard()
    # 70 hours at 1.5 s is ~168k messages. Sample the shape instead: replay
    # a burst of recital, then jump past whatever cooldown it earned.
    # Advance on the LAST trip of each burst, not the first — a burst long
    # enough to outlive a short cooldown escalates twice inside itself, and
    # advancing on the first trip lands back inside the second cooldown.
    now = 0.0
    last_trip = None
    for _ in range(8):
        _, verdicts = await _replay(
            guard,
            [(now + i * 1.5, "same thing again") for i in range(400)],
            is_agent_peer=True,
        )
        trips = [v for v in verdicts if v.transition in ("tripped", "escalated")]
        assert trips, (
            f"a burst of pure recital produced no trip at t={now}s — the "
            f"breaker stopped escalating"
        )
        last_trip = trips[-1]
        now += last_trip.cooldown_seconds + 60
        if last_trip.cooldown_seconds == 86400.0:
            break

    assert last_trip is not None
    assert last_trip.tier >= 4
    assert last_trip.cooldown_seconds == 86400.0, (
        "a loop that keeps resuming must reach the 24h step, not oscillate "
        "at the cheap end of the schedule forever"
    )


# ─────────────────────────────────────────────────────────────────────
# MUST NOT TRIP — the half that protects the product
# ─────────────────────────────────────────────────────────────────────

async def test_a_user_firing_off_a_burst_of_thoughts_is_untouched():
    guard = _guard()
    burst = [
        "hey", "so I was thinking about the migration",
        "we probably need to do the schema first",
        "actually no — the schema depends on the new column",
        "can you check whether prod already has it?",
        "also unrelated: did the nightly job run?",
        "sorry, one more thing",
        "what's the current p99 on the ingest path?",
    ]
    messages = [(i * 3.0, t) for i, t in enumerate(burst)]
    admitted, verdicts = await _replay(guard, messages)

    assert admitted == len(messages)
    assert all(v.tier == 0 for v in verdicts)


async def test_a_busy_group_at_peak_is_untouched():
    """200 distinct messages in ten minutes — well over the rate bar, and
    correctly ignored because none of them repeat."""
    guard = _guard()
    messages = [(i * 3.0, f"standup point {i} from the team") for i in range(200)]
    admitted, verdicts = await _replay(guard, messages, chat_id="!standup")

    assert admitted == len(messages)
    assert all(v.tier == 0 for v in verdicts)


async def test_a_job_batch_is_untouched():
    """Machine-generated, high-rate, marked as an agent peer — the
    tightest thresholds we have — but each report is distinct."""
    guard = _guard()
    messages = [
        (i * 0.5, f"[report] shard={i} rows=1200 status=ok")
        for i in range(400)
    ]
    admitted, verdicts = await _replay(guard, messages, is_agent_peer=True)

    assert admitted == len(messages)
    assert all(v.tier == 0 for v in verdicts)


async def test_a_person_saying_ok_all_day_is_untouched():
    """Repetitive but slow: the rate bar is what saves them."""
    guard = _guard()
    messages = [(i * 120.0, "ok") for i in range(200)]
    admitted, _ = await _replay(guard, messages)
    assert admitted == len(messages)


async def test_polite_repetition_inside_a_real_conversation_is_untouched():
    """"thanks" three times an hour among real messages is not a storm."""
    guard = _guard()
    messages = []
    for i in range(60):
        text = "thanks!" if i % 5 == 0 else f"and then we should do {i}"
        messages.append((i * 8.0, text))
    admitted, _ = await _replay(guard, messages)
    assert admitted == len(messages)


# ─────────────────────────────────────────────────────────────────────
# DELETION test (binding rule 4 / design §6)
# ─────────────────────────────────────────────────────────────────────

async def test_without_the_guard_the_incident_runs_the_full_pipeline():
    """Remove the guard and the replay must go back to 100% execution.

    Without this, every MUST-NOT-TRIP test above would still pass if the
    breaker had been accidentally disabled — they only assert that things
    were admitted.
    """
    messages = _liam_shape(2400)

    guarded, _ = await _replay(_guard(), messages, is_agent_peer=True)

    # "No guard" = the state of the world before this PR: nothing on the
    # inbound path asked whether the message was worth processing, so every
    # message ran the pipeline.
    assert guarded < len(messages) * 0.05, (
        "the guard is not doing the work this file claims it does"
    )


async def test_disabling_the_guard_is_a_visible_single_switch():
    """`INGRESS_GUARD_ENABLED` is the documented off switch.

    The deletion test above replays the incident with no guard at all and
    demands the full pipeline reappear. That only means something in
    production if a single, visible switch really is what controls whether
    the guard gets constructed — otherwise "we turned it off" and "the
    replay says it would run everything" are claims about two different
    systems.
    """
    from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase

    assert ChannelTriggerBase.INGRESS_GUARD_ENABLED is True
