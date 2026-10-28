"""
@file_name: test_ingress_breaker_persistence.py
@author:
@date: 2026-08-24
@description: A cooldown must outlive the process that set it.

The credential breaker in ``channel_trigger_base`` is deliberately
memory-only — it describes LIVE subscriber state, and a stopped trigger
must not carry isolation into its next start. The ingress breaker made
the opposite call, for one reason: the 8/14 loop ran 70+ HOURS. Any
redeploy inside that window would have handed a sender we had already
isolated for a day a completely fresh budget, and the incident would have
resumed with the platform believing it had never seen them.

Split of concerns pinned here:
  - sliding-window counts + fingerprints → memory only (they expire in
    ten minutes; a row per inbound message is pure write amplification)
  - tier + cooldown → written through on TRANSITION, read back on the
    first message a new process sees for that key
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from narranexus.platform.channel.ingress_guard import IngressGuard, content_fingerprint
from narranexus.platform.repository import ChannelIngressBreakerRepository

pytestmark = pytest.mark.asyncio

BASE = datetime(2026, 8, 14, 10, 22, 0, tzinfo=timezone.utc)
KEY = "agt_1|narramessenger|!room|@agent-liam"  # agent_id is part of the key


def _guard(repo) -> IngressGuard:
    return IngressGuard(
        repo=repo,
        window_seconds=600,
        rate_threshold=10,
        dup_ratio_threshold=0.8,
        schedule_seconds=(300.0, 1800.0, 7200.0, 86400.0),
    )


async def _storm(guard, *, start=BASE, n=12, text="ping"):
    out = []
    for i in range(n):
        out.append(
            await guard.admit(
                agent_id="agt_1",
                channel="narramessenger",
                chat_id="!room",
                sender_id="@agent-liam",
                fingerprint=content_fingerprint("!room", "@agent-liam", text),
                now=start + timedelta(seconds=i),
            )
        )
    return out


async def test_a_restart_does_not_hand_back_a_fresh_budget(db_client):
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))

    reborn = _guard(repo)  # the redeploy
    verdict = (await _storm(reborn, start=BASE + timedelta(seconds=30), n=1))[0]

    assert verdict.admit is False
    assert verdict.reason == "cooling"
    assert verdict.tier == 1


async def test_a_restart_after_the_cooldown_admits_a_probe_at_the_kept_tier(db_client):
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))

    reborn = _guard(repo)
    verdict = (await _storm(reborn, start=BASE + timedelta(seconds=400), n=1))[0]

    assert verdict.admit is True, "the cooldown has elapsed; probe it"
    assert verdict.tier == 1, (
        "escalation memory must survive the restart too — otherwise a "
        "re-offender restarts at the cheapest cooldown after every deploy"
    )


async def test_escalation_continues_across_a_restart(db_client):
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))

    reborn = _guard(repo)
    verdicts = await _storm(reborn, start=BASE + timedelta(seconds=400))
    escalated = [v for v in verdicts if v.transition == "escalated"]

    assert escalated, "a re-offending session must escalate after a restart"
    assert escalated[0].tier == 2
    assert escalated[0].cooldown_seconds == 1800.0, "the NEXT step, not the first"


async def test_only_transitions_are_written(db_client):
    """The hot path must not write. If it ever starts, an inbound storm
    becomes a DB storm — trading one outage for another."""
    repo = ChannelIngressBreakerRepository(db_client)
    writes: list[str] = []
    original = repo.upsert_state

    async def _counting(session_key, updates):
        writes.append(session_key)
        await original(session_key, updates)

    repo.upsert_state = _counting  # type: ignore[method-assign]

    guard = _guard(repo)
    await _storm(guard)                                    # 12 msgs → 1 trip
    await _storm(guard, start=BASE + timedelta(seconds=60), n=50)  # all cooling

    assert len(writes) == 1, (
        f"{len(writes)} durable writes for 62 inbound messages — only tier "
        f"transitions may write"
    )


async def test_a_closed_session_ages_out_but_an_isolated_one_does_not(db_client):
    """Retention must not forget the sessions we promised to remember."""
    repo = ChannelIngressBreakerRepository(db_client)
    await repo.upsert_state("chan|quiet|peer", {"channel": "chan", "tier": 0})
    await repo.upsert_state("chan|noisy|peer", {"channel": "chan", "tier": 3})

    deleted = await repo.cleanup_older_than_days(0)

    assert deleted == 1
    assert await repo.get("chan|quiet|peer") is None
    assert (await repo.get("chan|noisy|peer")).tier == 3


async def test_the_window_itself_is_not_persisted(db_client):
    """Sub-storm traffic leaves no row at all — nothing to clean up, no
    write amplification."""
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    for i in range(9):  # under the rate bar
        await guard.admit(
            agent_id="agt_1",
            channel="narramessenger",
            chat_id="!room",
            sender_id="@agent-liam",
            fingerprint=content_fingerprint("!room", "@agent-liam", "ping"),
            now=BASE + timedelta(seconds=i),
        )
    assert await repo.get(KEY) is None


async def test_the_absorbed_count_is_persisted_when_the_isolation_ends(db_client):
    """`suppressed_count` exists to answer "how much did this cooldown
    swallow?". The hot path deliberately never writes, so the probe is the
    only moment that number can reach the DB — writing 0 there (as the
    first version did) left the column stuck at 0 for its whole life.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await _storm(guard)

    # 50 messages arrive while the conversation is isolated.
    await _storm(guard, start=BASE + timedelta(seconds=60), n=50)

    # A NEW subject is a real probe (a repeat would re-trip on the spot).
    probe = (
        await _storm(
            guard, start=BASE + timedelta(seconds=400), n=1, text="new subject"
        )
    )[0]
    assert probe.transition == "probe"
    assert probe.suppressed >= 50

    row = await repo.get(KEY)
    assert row.suppressed_count == probe.suppressed, (
        "the durable column must agree with what the verdict reported"
    )


async def test_a_fresh_trip_resets_the_absorbed_count(db_client):
    """Each isolation reports its OWN absorption, not a lifetime total."""
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await _storm(guard)
    await _storm(guard, start=BASE + timedelta(seconds=60), n=50)
    await _storm(
        guard, start=BASE + timedelta(seconds=400), n=1, text="new subject"
    )  # probe

    # Re-offend: escalates, and the new isolation starts from zero.
    await _storm(guard, start=BASE + timedelta(seconds=500))
    row = await repo.get(KEY)
    assert row.tier == 2
    assert row.suppressed_count == 0


async def test_an_escalating_probe_still_reports_what_it_absorbed(db_client):
    """The re-trip path must not lose the ending isolation's count.

    ``_trip`` reads ``state.suppressed`` as "what the isolation that just
    ended absorbed". Zeroing it before that call (the first version of the
    half-open re-trip) made the escalation's audit row claim it had
    absorbed nothing.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await _storm(guard)
    await _storm(guard, start=BASE + timedelta(seconds=60), n=50)

    # Same recital resumes → re-trips on the probe itself.
    retrip = (await _storm(guard, start=BASE + timedelta(seconds=400), n=1))[0]
    assert retrip.transition == "escalated"
    assert retrip.reason == "probe_repeated"
    assert retrip.suppressed >= 50, "the ended isolation's absorption is lost"


async def test_warm_start_restores_the_standing_state_after_a_restart(db_client):
    """`/healthz` and the heartbeat read the IN-MEMORY counts.

    Without a warm start a restarted process holding isolated
    conversations reported `ingress_breaker_open_count: 0` — deploy, and
    the dashboard goes green while those conversations stay deaf. That is
    the L2-health blind spot (incident lesson #4) this design cites as its
    reason for existing.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))

    reborn = _guard(repo)
    assert reborn.open_session_count() == 0, "nothing loaded yet"

    restored = await reborn.warm_start("narramessenger", now=BASE + timedelta(seconds=60))
    assert restored == 1
    assert reborn.open_session_count() == 1
    assert reborn.cooling_session_count(BASE + timedelta(seconds=60)) == 1


async def test_warm_start_skips_a_session_whose_cooldown_has_elapsed(db_client):
    """Preloading is for the OBSERVABILITY surface, which only ever meant
    "currently suppressed" — so a lapsed row is not loaded at all.

    Loading every ``tier > 0`` row (the first version) would have made
    both the footprint and ``open_session_count()`` climb with every
    deploy: retention sweeps ``tier = 0`` exclusively, and a session that
    trips once then goes quiet never gets the ``admit()`` calls tier decay
    needs, so its row lives forever. "Reports 0 after a restart" would
    have become "reports a growing historical total" — equally useless
    for "how many are isolated right now".

    The escalation memory is not lost: ``_load()`` fetches it at the only
    moment it matters, when that key speaks again (asserted below).
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "tier": 2,
            # Lapsed, but only just. A day-old cooldown (the first version)
            # meant a day of silence AFTER the isolation ended, which now
            # correctly ages the tier out — the setup would then be pinning
            # the absence of decay rather than the lazy reload this test is
            # about.
            "cooldown_until": BASE - timedelta(seconds=60),
        },
    )
    guard = _guard(repo)
    restored = await guard.warm_start("narramessenger", now=BASE)

    assert restored == 0
    assert guard.open_session_count() == 0, "a lapsed row is not current state"

    # ...but the tier is still there the moment the conversation resumes.
    await _storm(guard, start=BASE, n=1, text="hello again")
    assert guard.open_session_count() == 1, "escalation memory lazily reloaded"


async def test_warm_start_does_not_inflate_with_history(db_client):
    """A long-lived deployment accumulates trip rows; the standing-state
    count must not accumulate with them."""
    repo = ChannelIngressBreakerRepository(db_client)
    for i in range(20):
        await repo.upsert_state(
            f"agt_{i}|narramessenger|!room|@peer",
            {
                "channel": "narramessenger",
                "agent_id": f"agt_{i}",
                "tier": 1,
                "cooldown_until": BASE - timedelta(days=3),
            },
        )
    # One conversation is genuinely still isolated.
    await repo.upsert_state(
        "agt_live|narramessenger|!room|@peer",
        {
            "channel": "narramessenger",
            "agent_id": "agt_live",
            "tier": 3,
            "cooldown_until": BASE + timedelta(hours=20),
        },
    )

    guard = _guard(repo)
    assert await guard.warm_start("narramessenger", now=BASE) == 1
    assert guard.open_session_count() == 1
    assert guard.cooling_session_count(BASE) == 1


async def test_warm_start_survives_a_broken_store(db_client):
    """Startup must not depend on it."""

    class _DeadRepo:
        async def find_open(self, channel=None, *, cooling_only=False, now=None):
            raise RuntimeError("db down")

    guard = _guard(_DeadRepo())
    assert await guard.warm_start("narramessenger") == 0


async def test_session_key_cannot_overflow_its_column():
    """The key's width must be DERIVED from the DDL, not asserted next to it.

    The first clamp used one number (128) for all four parts, which allows
    4*128 + 3 = 515 — over the 448 the column was sized for, because
    ``channel`` is only VARCHAR(32). The comment above it claimed the
    arithmetic was "true by construction"; nobody had run the
    multiplication. This test runs it, against the registry, so the claim
    cannot drift from the schema again.
    """
    from narranexus.platform.schema.channel_ingress_breaker_schema import session_key
    from narranexus.platform.utils.db.schema_registry import varchar_width

    worst = session_key("a" * 4000, "b" * 4000, "c" * 4000, "d" * 4000)
    limit = varchar_width("channel_ingress_breaker", "session_key")
    assert len(worst) <= limit, (
        f"a maximal key is {len(worst)} chars but session_key is "
        f"VARCHAR({limit}) — on a MySQL deployment without strict mode this "
        f"truncates, and two conversations collide on the unique index"
    )

    # And each component stays inside its own column too.
    parts = dict(zip(("agent_id", "channel", "chat_id", "sender_id"), worst.split("|")))
    for name, value in parts.items():
        assert len(value) <= varchar_width("channel_ingress_breaker", name), name


async def test_a_session_that_went_silent_ages_its_tier_down(db_client):
    """Escalation memory must expire with TIME, not only with traffic.

    `_maybe_recover` runs from `admit()`, so it walks a tier down only
    while the session KEEPS TALKING. A session that trips and then goes
    quiet is never visited again: nothing polls, and the durable row is
    read back verbatim. The tier would stand for the life of the row.

    That is the failure `_maybe_recover`'s own docstring names — a
    conversation that misbehaved once carrying a 24h-capable tier forever,
    so the first bad minute a year later costs a day.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))  # trips to tier 1

    guard = _guard(repo)
    step = guard._decay_step_seconds

    # Comes back after enough silence for exactly one step. The clock
    # starts when the ISOLATION ENDS (BASE + 300), not when it began:
    # serving the cooldown is not silence, and counting it would bill the
    # sentence as good behaviour.
    start = BASE + timedelta(seconds=300 + step + 60)
    verdict = (await _storm(guard, start=start, n=1))[0]

    assert verdict.admit is True
    assert verdict.tier == 0, (
        "silence past one decay step must have aged the tier down, not "
        f"handed the session back its escalation memory: tier={verdict.tier}"
    )


async def test_ageing_down_to_zero_makes_the_row_sweepable(db_client):
    """The other half of the same defect: `cleanup_older_than_days`
    deliberately sweeps only `tier = 0`, so a session stuck above zero is
    a row nothing can ever reclaim. Ageing must be written back."""
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))

    row = await repo.get(KEY)
    assert row is not None and row.tier == 1

    guard = _guard(repo)
    # Measured from the end of the 300s cooldown, not from the trip.
    await _storm(
        guard,
        start=BASE + timedelta(seconds=300 + guard._decay_step_seconds + 60),
        n=1,
    )

    row = await repo.get(KEY)
    assert row is not None
    assert row.tier == 0, "the durable row still carries a tier nothing can sweep"
    assert row.last_reason == "aged_out"


async def test_many_silent_steps_age_down_by_many_tiers(db_client):
    """The catch-up is proportional to the silence, not one step per load.

    A row six months old must not need six months of restarts to come
    back to zero.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 3,
            "cooldown_until": None,
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )

    later = BASE + timedelta(seconds=guard._decay_step_seconds * 3 + 60)
    verdict = (await _storm(guard, start=later, n=1))[0]
    assert verdict.tier == 0, f"three steps of silence, tier={verdict.tier}"


async def test_ageing_never_releases_a_session_still_in_its_cooldown(db_client):
    """Decaying inside the cooldown would end the isolation early — the one
    thing this state exists to hold. The 8/14 loop ran 70+ hours; a reload
    partway through must not shorten the sentence."""
    repo = ChannelIngressBreakerRepository(db_client)
    await _storm(_guard(repo))  # tier 1, cooldown 300s from BASE

    guard = _guard(repo)
    verdict = (await _storm(guard, start=BASE + timedelta(seconds=120), n=1))[0]

    assert verdict.admit is False, "still inside the cooldown"
    assert verdict.tier == 1, "the tier must not have aged while isolated"


async def test_ageing_does_not_run_while_the_isolation_is_still_in_force(db_client):
    """Long cooldowns outlive a decay step, so the guard must be explicit.

    At tier 3 the cooldown is 7200s while a decay step is 1200s. A reload
    two thousand seconds in is therefore both "still isolated" and "past a
    step" — the one timeline where ageing would cut the sentence short.
    The 8/14 loop ran 70+ hours; a redeploy partway through must not
    shorten it.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 3,
            "cooldown_until": BASE + timedelta(seconds=7200),
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )

    mid = BASE + timedelta(seconds=guard._decay_step_seconds + 100)
    assert mid < BASE + timedelta(seconds=7200), "the timeline must still be cooling"
    verdict = (await _storm(guard, start=mid, n=1))[0]

    assert verdict.admit is False, "still isolated"
    assert verdict.tier == 3, (
        f"ageing released the isolation early: tier={verdict.tier}"
    )


async def test_a_recovery_step_refreshes_the_decay_anchor(db_client):
    """`_maybe_recover` and `_decay_for_silence` must not both bill the
    same silence.

    The anchor is `tier_changed_at`, and a recovery IS a tier change. If a
    recovery left it standing, the next load would count the time already
    paid out by `_maybe_recover` a second time and over-decay — handing a
    re-offender a cheaper cooldown, which is what the tier exists to deny.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await _storm(guard)  # trips to tier 1 at BASE

    tripped = await repo.get(KEY)
    assert tripped is not None and tripped.tier_changed_at is not None

    # Same instance throughout: the durable catch-up in `_load` only runs
    # on a fresh guard, and it would zero this tier before `_maybe_recover`
    # ever saw it. This is the "kept talking" half of the decay.
    step = guard._decay_step_seconds
    # The first message after the cooldown is the half-open probe; it
    # starts the clean clock rather than earning a step.
    await _storm(guard, start=BASE + timedelta(seconds=400), n=1, text="probe")
    await _storm(guard, start=BASE + timedelta(seconds=400 + step + 60), n=1,
                 text="unrelated")

    row = await repo.get(KEY)
    assert row is not None
    assert row.last_reason == "recovered"
    assert row.tier_changed_at is not None
    assert row.tier_changed_at > tripped.tier_changed_at, (
        "a recovery left the decay anchor at the trip time; the silence it "
        "just consumed would be billed again on the next load"
    )


def _fake_open_rows(n, *, cooling):
    return [
        {
            "session_key": f"k{i}",
            "channel": "slack",
            "agent_id": "a",
            "chat_id": "c",
            "sender_id": "s",
            "tier": 1,
            "cooldown_until": (BASE + timedelta(seconds=7200)) if cooling else None,
            "suppressed_count": 0,
            "last_reason": None,
            "last_tripped_at": None,
            "tier_changed_at": None,
            "created_at": None,
            "updated_at": None,
        }
        for i in range(n)
    ]


async def _capped_find_open(rows, *, cooling_only=False, now=None):
    """Drive the real repository against a db handle that returns `rows`."""
    from loguru import logger

    calls = []
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:

        class _FakeDb:
            async def get(self, table, filters=None, **kwargs):
                calls.append(kwargs)
                return rows

        out = await ChannelIngressBreakerRepository(_FakeDb()).find_open(
            "slack", cooling_only=cooling_only, now=now
        )
    finally:
        logger.remove(sink)
    return calls, out, lines


async def test_find_open_is_bounded():
    """`tier > 0` and "still cooling" are both decided in Python, so an
    unbounded query here is a full-table read on every process start."""
    from narranexus.platform.repository.channel_ingress_breaker_repository import (
        _FIND_OPEN_LIMIT,
    )

    calls, rows, _ = await _capped_find_open(
        _fake_open_rows(_FIND_OPEN_LIMIT + 50, cooling=True)
    )
    assert calls and calls[0].get("limit") is not None, (
        "find_open issued an unbounded read"
    )
    assert len(rows) == _FIND_OPEN_LIMIT, f"cap not applied: {len(rows)}"


async def test_the_cap_warns_only_when_a_candidate_could_have_been_cut():
    """The warning has to be worth reading, and "candidate" depends on the
    question the caller asked.

    With `cooling_only=True` only rows still cooling are candidates, and
    `ORDER BY cooldown_until DESC` sorts NULLs last in both dialects — so
    the cut tail can only hold a candidate if the last row KEPT is itself
    still cooling. An already-elapsed timestamp there means nothing was
    lost, and warning anyway fires on every boot for any channel with a
    long trip history. A warning that cries wolf each boot gets filtered,
    after which a real truncation is as invisible as a silent one.

    With `cooling_only=False` the candidates are all tier > 0 rows and the
    cut tail is exactly the NULL-cooldown ones, which may well be tier > 0.
    There, any truncation may have lost one.
    """
    from narranexus.platform.repository.channel_ingress_breaker_repository import (
        _FIND_OPEN_LIMIT,
    )

    n = _FIND_OPEN_LIMIT + 50
    during = BASE + timedelta(seconds=60)
    after = BASE + timedelta(seconds=99999)

    _, _, quiet = await _capped_find_open(
        _fake_open_rows(n, cooling=True), cooling_only=True, now=after
    )
    assert not [ln for ln in quiet if "WARNING" in ln], (
        f"warned although every cut row was past its cooldown: {quiet}"
    )

    _, _, loud = await _capped_find_open(
        _fake_open_rows(n, cooling=True), cooling_only=True, now=during
    )
    assert [ln for ln in loud if "WARNING" in ln], (
        f"the last kept row was still cooling and nothing was said: {loud}"
    )

    # The tail-is-NULL shape: `last_kept` comes back None rather than an
    # elapsed timestamp. Same silent branch, different way in — it was the
    # original quiet case and lost its coverage when the criterion moved
    # from "non-null" to "still cooling".
    _, _, null_tail = await _capped_find_open(
        _fake_open_rows(n, cooling=False), cooling_only=True, now=during
    )
    assert not [ln for ln in null_tail if "WARNING" in ln], (
        f"warned although the cut tail carried no cooldown at all: {null_tail}"
    )

    # The other question: escalation memory, cooldown irrelevant.
    _, _, always = await _capped_find_open(
        _fake_open_rows(n, cooling=False), cooling_only=False
    )
    assert [ln for ln in always if "WARNING" in ln], (
        "cooling_only=False counts every tier > 0 row as a candidate, so "
        f"any truncation may have dropped one: {always}"
    )


async def test_serving_a_long_cooldown_is_not_credited_as_silence(db_client):
    """The mirror of `test_ageing_does_not_run_while_...`, on the other side
    of the cooldown boundary.

    `tier_changed_at` is stamped when the session trips, and the cooldown
    runs from there. Anchoring the catch-up on it counts the isolation
    itself as good behaviour — and a high tier's cooldown is many decay
    steps long. At tier 3 (7200s cooldown, 1200s step) that is six free
    steps: the first message after the cooldown elapsed zeroes the
    escalation memory outright, and a persistent loop goes back to the
    cheapest 300s tier after every deploy.

    That is what `test_a_restart_after_the_cooldown_admits_a_probe_at_the_kept_tier`
    says it prevents; that test only passes on tier 1, whose 300s cooldown
    is the one case of the four shorter than a single decay step.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 3,
            "cooldown_until": BASE + timedelta(seconds=7200),
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )
    assert 7200 > guard._decay_step_seconds, (
        "the point of this test is a cooldown longer than a decay step"
    )

    # Just past the cooldown: nothing has been silent yet.
    verdict = (await _storm(guard, start=BASE + timedelta(seconds=7300), n=1))[0]

    assert verdict.admit is True, "the cooldown has elapsed; probe it"
    assert verdict.tier == 3, (
        "time spent isolated was credited as silence, so a re-offender "
        f"restarts at the cheapest cooldown after a deploy: tier={verdict.tier}"
    )

    # The durable half alone is not the guarantee: the in-memory anchor has
    # to carry the (now elapsed) cooldown too, or the sweep undoes this
    # decision a moment later. Without the second assertion this test
    # reports "protected" while half the path is open.
    state = guard._sessions[KEY]
    guard.prune_idle(BASE + timedelta(seconds=7300))
    assert state.tier == 3, (
        f"the sweep re-billed the served cooldown as silence: {state.tier}"
    )


async def test_a_zero_window_degrades_instead_of_raising(db_client):
    """`admit()` must not be the one place in this class that raises.

    `get` / `upsert_state` / `_persist` / `warm_start` / `_load` all
    degrade rather than throw, because this guard sits on the inbound path
    and is not an authorization gate. The decay arithmetic divides by
    `window * recovery_windows`, so a zero window would send a
    ZeroDivisionError out of `admit()` and drop every message on the
    channel — the user-visible content loss binding rule #16 forbids, from
    a value a per-channel config could plausibly carry.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 2,
            "cooldown_until": None,
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )
    guard = IngressGuard(
        repo=repo,
        window_seconds=0,
        rate_threshold=10,
        dup_ratio_threshold=0.8,
        schedule_seconds=(300.0, 1800.0, 7200.0, 86400.0),
    )

    verdict = await guard.admit(
        agent_id="agt_1",
        channel="narramessenger",
        chat_id="!room",
        sender_id="@agent-liam",
        fingerprint=content_fingerprint("!room", "@agent-liam", "hi"),
        now=BASE + timedelta(seconds=99999),
    )
    assert verdict is not None, "a zero window must degrade, not raise"


async def test_a_load_time_age_down_is_not_billed_again_by_the_sweep(db_client):
    """Two decay paths, one clock. They must not both charge for the same
    silence.

    `_load` ages the tier down from the durable anchor; `prune_idle` ages
    it down from the in-memory one. If the load path does not move the
    in-memory anchor forward, the very next sweep recomputes the same
    elapsed time and takes another step — one step of silence spending
    twice, which is the double-credit this anchor was introduced to stop,
    arriving from the other direction.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 3,
            "cooldown_until": None,
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )
    guard = _guard(repo)
    at = BASE + timedelta(seconds=guard._decay_step_seconds + 60)

    verdict = (await _storm(guard, start=at, n=1))[0]
    assert verdict.tier == 2, f"one step of silence, one step down: {verdict.tier}"

    state = guard._sessions[KEY]
    guard.prune_idle(at)
    assert state.tier == 2, (
        f"the sweep charged for the silence the load already spent: {state.tier}"
    )


async def test_the_sweep_can_age_down_a_session_it_only_read_from_the_db(db_client):
    """A tier > 0 session is pinned in memory by `prune_idle` on purpose,
    so it never gets a second `_load`. If the durable anchor is not carried
    into memory when the row is first read, the sweep can only ever age
    down sessions that tripped inside THIS process — and a long-running
    trigger (binding rule #14) mostly holds sessions it loaded, not ones it
    watched trip.

    Observable through `open_session_count()`, the L2 metric this design
    offers against the 8/14 blind spot: without the sweep reaching these,
    it drifts from "isolated right now" toward "tripped since the last
    deploy".
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 1,
            "cooldown_until": None,
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )
    guard = _guard(repo)
    step = guard._decay_step_seconds

    # One message brings the row into memory without earning any decay.
    await _storm(guard, start=BASE + timedelta(seconds=10), n=1)
    assert guard.open_session_count() == 1

    # Now only the sweep runs — no further traffic on this session.
    guard.prune_idle(BASE + timedelta(seconds=10 + step + 60))
    assert guard.open_session_count() == 0, (
        "the sweep never reached a session loaded from the DB, so its tier "
        "stands for the life of the process"
    )
    # The other half: aged to 0 is not enough, the entry has to actually
    # GO. While it is held, `state.loaded` stays True, `_load` never runs
    # again, and the durable row is never corrected to `aged_out` — the
    # justification for the sweep being memory-only.
    assert KEY not in guard._sessions, (
        "aged down but still held: the row it belongs to can never be "
        "swept, which is the growth this whole path exists to stop"
    )


async def test_warm_start_sessions_are_reachable_by_the_sweep(db_client):
    """Same property on the other construction path.

    `warm_start` loads only rows still cooling, which is what keeps
    `open_session_count()` meaning "isolated now" rather than "ever
    tripped". That guarantee only holds if those sessions can also LEAVE
    the count once their cooldown elapses and the silence is served.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 1,
            "cooldown_until": BASE + timedelta(seconds=300),
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )
    guard = _guard(repo)
    restored = await guard.warm_start("narramessenger", now=BASE + timedelta(seconds=60))
    assert restored == 1
    assert guard.open_session_count() == 1

    step = guard._decay_step_seconds
    guard.prune_idle(BASE + timedelta(seconds=300 + step + 60))
    assert guard.open_session_count() == 0, (
        "a warm-started session can never leave the standing-isolation "
        "count, so the gauge climbs with uptime"
    )
    assert KEY not in guard._sessions, (
        "a warm-started session carries a non-null cooldown forever, so a "
        "criterion of 'never had one' pins it in memory permanently"
    )

    # Dropping is not fully transparent, and the difference is declared in
    # `prune_idle`'s docstring: the reload keeps only a cooldown still in
    # the future, so the next message takes the ordinary counting path
    # rather than arriving as a half-open probe. Asserted so that
    # "restoring an elapsed cooldown for transparency" cannot silently
    # reverse it. Deliberately no assertion on tier — that would depend on
    # which step of the schedule this session happens to be on.
    resumed = (await _storm(guard, start=BASE + timedelta(seconds=300 + step + 120),
                            n=1, text="back again"))[0]
    assert resumed.admit is True
    assert resumed.transition != "probe", (
        f"a dropped session came back as a half-open probe: {resumed}"
    )


async def test_a_restart_after_a_probe_does_not_age_out_the_tier(db_client):
    """The durable half of the same defect.

    The probe writes `cooldown_until = NULL`. `upsert_state` is a partial
    write, so that really is the end of the only column recording when the
    isolation finished — unless the probe also moves `tier_changed_at`
    forward. Without it, the next process to read this row measures silence
    from the trip, counts the whole cooldown, and writes `aged_out`.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 3,
            "cooldown_until": BASE + timedelta(seconds=7200),
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )

    # The session must be held IN MEMORY across the cooldown boundary for
    # `_half_open` to run at all: a lazy load after the cooldown already
    # elapsed simply drops `state.cooldown_until` and admits normally, so
    # there is no probe transition and nothing gets persisted. Load it
    # while still cooling, then cross the boundary inside this process.
    (await _storm(guard, start=BASE + timedelta(seconds=60), n=1))[0]
    assert guard._sessions[KEY].cooldown_until is not None, "must load as cooling"

    probe_at = BASE + timedelta(seconds=7250)
    verdict = (await _storm(guard, start=probe_at, n=1, text="brand new"))[0]
    assert verdict.transition == "probe", f"expected the half-open probe: {verdict}"
    assert verdict.tier == 3, f"the probe should keep the tier: {verdict}"

    # Another restart, shortly afterwards.
    reborn = _guard(repo)
    later = (await _storm(reborn, start=probe_at + timedelta(seconds=60),
                          n=1, text="and another"))[0]

    assert later.tier == 3, (
        "the served cooldown was re-billed as silence after the probe "
        f"erased it, so the escalation memory is gone: tier={later.tier}"
    )
    row = await repo.get(KEY)
    assert row is not None and row.last_reason != "aged_out", (
        f"the row was aged out by time already served: {row.last_reason}"
    )


async def test_warm_start_carries_the_isolation_end_into_the_anchor(db_client):
    """`warm_start` keeps only a FUTURE cooldown in memory, so the row's
    end-of-isolation has to be folded into the anchor as it is read.

    Otherwise the anchor falls back to the trip time and the first sweep
    after the cooldown lapses counts the whole sentence as silence — six
    steps at tier 3, so the escalation memory is gone one sweep after a
    restart that was supposed to preserve it.

    Tier 3 on purpose: with tier 1's 300s cooldown the session ages out
    either way, which is why the existing warm-start test cannot see this.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 3,
            "cooldown_until": BASE + timedelta(seconds=7200),
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )
    guard = _guard(repo)
    restored = await guard.warm_start(
        "narramessenger", now=BASE + timedelta(seconds=60)
    )
    assert restored == 1

    state = guard._sessions[KEY]
    # Just past the cooldown: no silence has been served yet.
    guard.prune_idle(BASE + timedelta(seconds=7250))

    assert state.tier == 3, (
        "the sweep counted the served cooldown as silence, so a restart "
        f"during isolation still loses the tier: {state.tier}"
    )


async def test_a_probe_persists_the_tier_the_sweep_left_behind(db_client):
    """`tier` and `tier_changed_at` are two halves of one fact.

    The in-memory sweep is the only path that moves a tier without
    persisting it, and its justification is that the row still says tier N
    with an untouched anchor, so the next `_load` recomputes the same
    answer. A probe that writes the new anchor but keeps the old tier
    destroys that: the reload sees an old tier with a fresh anchor and the
    served sentence grows back.

    Pinned on an INTERMEDIATE tier, and driven through `prune_idle` rather
    than the private sweep. The zero case cannot occur through the public
    API at all — `prune_idle` ages a session to 0 and drops it on the SAME
    pass (an entry carrying a cooldown always has an empty event queue, so
    all three stale conditions hold at once). Only `tier > 0` survives the
    sweep, so tier 3 → 2 → probe is the one shape this defect can still
    reach.

    Tier 3 also because a 7200s cooldown is longer than a 1200s decay
    step; at tier 1 the step count is always zero and nothing is
    observable — the sampling blind spot this PR has hit three times.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    guard = _guard(repo)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 3,
            "cooldown_until": BASE + timedelta(seconds=7200),
            "last_reason": "escalated",
            "tier_changed_at": BASE,
        },
    )
    # Held in memory across the cooldown boundary, or `_half_open` never
    # runs (a lazy load after it lapsed just admits normally).
    await _storm(guard, start=BASE + timedelta(seconds=60), n=1)
    state = guard._sessions[KEY]
    assert state.tier == 3, f"setup: {state.tier}"

    # Exactly one decay step past the end of the isolation: one step down,
    # and still above zero so the entry survives the sweep.
    swept_at = BASE + timedelta(seconds=7200 + guard._decay_step_seconds + 60)
    guard.prune_idle(swept_at)
    assert KEY in guard._sessions, "a tier > 0 session must survive the sweep"
    assert state.tier == 2, f"setup: expected one step down, got {state.tier}"

    # Now the probe fires on the same in-memory session.
    verdict = (await _storm(guard, start=swept_at, n=1, text="brand new"))[0]
    assert verdict.transition == "probe", f"expected the half-open probe: {verdict}"

    row = await repo.get(KEY)
    assert row is not None
    assert row.tier == state.tier, (
        f"the probe wrote a fresh anchor onto a stale tier: row={row.tier} "
        f"memory={state.tier}"
    )

    reborn = _guard(repo)
    later = (await _storm(reborn, start=swept_at + timedelta(seconds=60), n=1))[0]
    assert later.tier == 2, (
        f"the served sentence grew back across the reload: {later.tier}"
    )


async def test_a_row_with_no_anchor_still_decays_from_its_cooldown_end(db_client):
    """A row that records an isolation end but no tier move.

    The two halves of the fold used to disagree here: reading the row into
    memory produced a non-null anchor (falling back to the cooldown end)
    while the decay short-circuited on the null `tier_changed_at` and
    returned zero steps — two answers for one row. With one definition,
    both say "measure from the end of the isolation".

    This is a behaviour CHANGE, not a refactor: such a row now decays.
    """
    repo = ChannelIngressBreakerRepository(db_client)
    await repo.upsert_state(
        KEY,
        {
            "channel": "narramessenger",
            "agent_id": "agt_1",
            "chat_id": "!room",
            "sender_id": "@agent-liam",
            "tier": 2,
            "cooldown_until": BASE + timedelta(seconds=300),
            "last_reason": "escalated",
            # tier_changed_at deliberately absent.
        },
    )
    guard = _guard(repo)
    row = await repo.get(KEY)
    assert row is not None and row.tier_changed_at is None, "setup: no anchor"

    # Two decay steps of silence after the isolation ended.
    at = BASE + timedelta(seconds=300 + guard._decay_step_seconds * 2 + 60)
    verdict = (await _storm(guard, start=at, n=1))[0]

    assert verdict.tier == 0, (
        "a row carrying only an isolation end must decay from it, not be "
        f"treated as having no origin at all: tier={verdict.tier}"
    )
