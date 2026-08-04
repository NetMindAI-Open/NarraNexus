"""
@file_name: test_credential_breaker.py
@author: Bin Liang
@date: 2026-08-04
@description: Fast-death circuit breaker + unstartable-credential pre-flight.

Regression (prod, 2026-08-01..03): an agent's LarkSuite App Secret was
cleared, so its subscriber exited immediately on every start (a silent
`return`, not an exception — `is_permanent_auth_failure` never sees it).
`_credential_watcher` reaped the dead task and unconditionally restarted
the same key on the next poll, forever: ~28s death/rebirth cycles flooding
the logs (1498 restarts in a 4h window) with no isolation and no recovery
path other than manually deleting the credential row.

Two layers of defence, both pinned here:

1. ``should_start_subscriber`` — a pre-flight the channel can answer from
   the credential alone (Lark: a cleared secret can never connect). Costs
   zero restarts for a knowable-in-advance condition.
2. The breaker itself — the generic backstop for everything unknowable in
   advance. Counts consecutive fast deaths per key and isolates on a
   schedule.

The fingerprint tests carry the sharpest edge: the fingerprint must be
sampled from the credential the dead subscriber was STARTED with (not the
watcher's cache, which is a poll behind) AND must exclude fields the
subscriber itself writes back (Matrix's `matrix_since_token` is documented
WRITE-HEAVY). Get either wrong and the breaker clears itself the instant it
trips — the restart storm this whole file exists to stop.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import pytest

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_HEARTBEAT,
    EVENT_SUBSCRIBER_BREAKER_CLEARED,
    EVENT_SUBSCRIBER_BREAKER_TRIPPED,
    EVENT_SUBSCRIBER_UNSTARTABLE,
)
from xyz_agent_context.channel.channel_health_server import _snapshot_one
from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.schema.hook_schema import WorkingSource


@dataclass
class _Credential:
    agent_id: str = "agent_a"
    app_id: str = "bot1"
    app_secret: str = "secret"
    # Stands in for a WRITE-HEAVY bookkeeping column (Matrix's since_token).
    cursor: str = ""
    updated_at: str = ""


class _CaptureAuditRepo:
    def __init__(self):
        self.rows = []

    async def append(self, event_type, **kwargs):
        self.rows.append((event_type, kwargs))

    def types(self):
        return [t for t, _ in self.rows]

    def details_for(self, event_type):
        return [kw.get("details", {}) for t, kw in self.rows if t == event_type]


class _BreakerTrigger(ChannelTriggerBase):
    """Subscriber dies instantly (empty-secret shape: return, no raise)."""

    channel_name = "fake"
    brand_display = "Fake"
    working_source = WorkingSource.LARK
    CREDENTIAL_POLL_INTERVAL_SECONDS = 0.05
    IDLE_POLL_INTERVAL_SECONDS = 0.05
    BREAKER_BACKOFF_SCHEDULE_SECONDS = (9999.0,)
    BREAKER_VOLATILE_CREDENTIAL_FIELDS = frozenset({"created_at", "updated_at", "cursor"})

    def __init__(self):
        super().__init__(base_workers=1)
        self.loop_starts = 0
        self.creds: list[_Credential] = [_Credential()]
        self.startable = True
        self.keep_subscriber_alive = False

    async def load_active_credentials(self):
        return list(self.creds)

    def should_start_subscriber(self, credential):
        return self.startable

    async def _subscribe_loop(self, credential):
        self.loop_starts += 1
        if self.keep_subscriber_alive:
            await asyncio.Event().wait()

    async def connect(self, credential):  # pragma: no cover - loop overridden
        if False:
            yield {}

    def parse_event(self, raw):  # pragma: no cover - unused
        return None

    async def is_echo(self, message, credential):
        return False

    async def resolve_sender_name(self, sender_id, credential):
        return "x"

    def create_context_builder(self, message, credential, agent_id):
        return None


def _armed(trigger: _BreakerTrigger, cred: _Credential, *, lifetime: float = 0.0):
    """Put `key` in the state a just-died subscriber leaves behind."""
    key = trigger._subscriber_key(cred)
    trigger._subscriber_started_at[key] = time.monotonic() - lifetime
    trigger._subscriber_start_fingerprint[key] = trigger._credential_fingerprint(cred)
    return key


async def _kill_until_tripped(trigger: _BreakerTrigger, cred: _Credential):
    """Drive exactly THRESHOLD fast deaths; returns the subscriber key."""
    key = trigger._subscriber_key(cred)
    for _ in range(trigger.BREAKER_FAST_DEATH_THRESHOLD):
        _armed(trigger, cred)
        await trigger._breaker_record_death(key, cred)
    return key


# ── The breaker's own state machine (no event-loop timing involved) ────────

@pytest.mark.asyncio
async def test_fast_deaths_trip_only_at_threshold():
    trigger = _BreakerTrigger()
    trigger._audit_repo = _CaptureAuditRepo()
    cred = _Credential()
    key = trigger._subscriber_key(cred)

    for _ in range(trigger.BREAKER_FAST_DEATH_THRESHOLD - 1):
        _armed(trigger, cred)
        await trigger._breaker_record_death(key, cred)
    assert key not in trigger._breaker_blocked_until
    assert await trigger._breaker_admit(key, cred) is True

    _armed(trigger, cred)
    await trigger._breaker_record_death(key, cred)
    assert key in trigger._breaker_blocked_until
    assert await trigger._breaker_admit(key, cred) is False
    assert trigger._audit_repo.types().count(EVENT_SUBSCRIBER_BREAKER_TRIPPED) == 1


@pytest.mark.asyncio
async def test_healthy_lifetime_resets_streak():
    trigger = _BreakerTrigger()
    cred = _Credential()
    key = trigger._subscriber_key(cred)

    for _ in range(trigger.BREAKER_FAST_DEATH_THRESHOLD - 1):
        _armed(trigger, cred)
        await trigger._breaker_record_death(key, cred)
    # One healthy session wipes the streak...
    _armed(trigger, cred, lifetime=trigger.BREAKER_FAST_DEATH_SECONDS + 1)
    await trigger._breaker_record_death(key, cred)
    assert trigger._breaker_fast_deaths.get(key, 0) == 0

    # ...so the next fast death starts counting from one, not from N-1.
    _armed(trigger, cred)
    await trigger._breaker_record_death(key, cred)
    assert key not in trigger._breaker_blocked_until


@pytest.mark.asyncio
async def test_intentional_stop_is_not_a_death():
    """`_stop_subscriber` drops the start marks, so a deliberate stop can
    never be counted toward the fast-death streak."""
    trigger = _BreakerTrigger()
    cred = _Credential()
    key = _armed(trigger, cred)

    await trigger._stop_subscriber(key)
    await trigger._breaker_record_death(key, cred)

    assert trigger._breaker_fast_deaths.get(key, 0) == 0
    assert key not in trigger._breaker_blocked_until


@pytest.mark.asyncio
async def test_backoff_tiers_escalate_and_audit_each_reprobe():
    trigger = _BreakerTrigger()
    trigger.BREAKER_BACKOFF_SCHEDULE_SECONDS = (10.0, 900.0)
    trigger._audit_repo = _CaptureAuditRepo()
    cred = _Credential()

    key = await _kill_until_tripped(trigger, cred)
    first = trigger._breaker_blocked_until[key] - time.monotonic()
    assert 5 < first <= 10

    # Backoff expiry re-admits and leaves a DB trace of the re-probe.
    trigger._breaker_blocked_until[key] = time.monotonic() - 0.001
    assert await trigger._breaker_admit(key, cred) is True
    reasons = [
        d.get("reason") for d in trigger._audit_repo.details_for(EVENT_SUBSCRIBER_BREAKER_CLEARED)
    ]
    assert reasons == ["backoff_expired"]

    # The re-probe needs a fresh full streak, then lands on tier 2.
    await _kill_until_tripped(trigger, cred)
    second = trigger._breaker_blocked_until[key] - time.monotonic()
    assert second > 100


@pytest.mark.asyncio
async def test_watcher_resets_backoff_tier_once_reprobe_proves_healthy(db_client):
    """A re-probe that stays alive past the fast-death window earns a reset
    while it is still running; it must not have to die to prove health."""
    trigger = _BreakerTrigger()
    trigger.BREAKER_BACKOFF_SCHEDULE_SECONDS = (0.01, 900.0)
    trigger.keep_subscriber_alive = True
    cred = _Credential()
    key = await _kill_until_tripped(trigger, cred)
    assert trigger._breaker_trips[key] == 1

    trigger._breaker_blocked_until[key] = time.monotonic() - 0.001
    await trigger.start(db_client)
    try:
        assert await _wait_until(lambda: key in trigger._subscriber_tasks)
        # Advance only the subscriber's age marker; do not sleep through the
        # production 60-second proof window or patch asyncio's clock.
        trigger._subscriber_started_at[key] = (
            time.monotonic() - trigger.BREAKER_FAST_DEATH_SECONDS - 1
        )
        assert await _wait_until(lambda: key not in trigger._breaker_trips)

        # The next incident starts from tier 1, not the preserved tier 2.
        trigger.running = False
        for task in trigger._monitor_tasks:
            task.cancel()
        await trigger._stop_subscriber(key)
        await _kill_until_tripped(trigger, cred)
        assert trigger._breaker_blocked_until[key] - time.monotonic() < 1.0
    finally:
        await trigger.stop()


@pytest.mark.asyncio
async def test_watcher_does_not_settle_task_that_finishes_during_reap():
    """A task can finish while another dead task's audit write is awaited.
    Remaining in the task map until the next reap must not count as healthy."""
    trigger = _BreakerTrigger()
    first = _Credential(app_id="bot1")
    second = _Credential(app_id="bot2")
    first_key = trigger._subscriber_key(first)
    second_key = trigger._subscriber_key(second)
    finish_second = asyncio.Event()

    first_task = asyncio.create_task(asyncio.sleep(0))
    await first_task

    async def finish_on_signal():
        await finish_second.wait()

    second_task = asyncio.create_task(finish_on_signal())
    await asyncio.sleep(0)
    trigger.creds = [first, second]
    trigger._subscriber_tasks = {
        first_key: first_task,
        second_key: second_task,
    }
    trigger._subscriber_creds = {first_key: first, second_key: second}
    trigger._subscriber_started_at[second_key] = (
        time.monotonic() - trigger.BREAKER_FAST_DEATH_SECONDS - 1
    )
    trigger._subscriber_start_fingerprint[second_key] = (
        trigger._credential_fingerprint(second)
    )
    trigger._breaker_trips[second_key] = 1

    async def record_first_death(_key, _credential):
        finish_second.set()
        await second_task
        trigger.running = False

    trigger._breaker_record_death = record_first_death
    trigger.running = True

    try:
        await trigger._credential_watcher()
        assert second_task.done()
        assert second_key in trigger._breaker_trips
    finally:
        await trigger.stop()


# ── Fingerprint: the edge that decides whether the breaker holds ──────────

@pytest.mark.asyncio
async def test_volatile_field_churn_does_not_clear_breaker():
    """Matrix writes `since_token` on every sync. If that churn read as a
    re-bind, the breaker would clear the instant it tripped and the restart
    storm would return."""
    trigger = _BreakerTrigger()
    cred = _Credential(cursor="s_1")
    key = await _kill_until_tripped(trigger, cred)

    churned = _Credential(cursor="s_2", updated_at="2026-08-04T09:00:00Z")
    assert await trigger._breaker_admit(key, churned) is False
    assert key in trigger._breaker_blocked_until


@pytest.mark.asyncio
async def test_start_time_fingerprint_survives_a_stale_watcher_cache():
    """The fingerprint must come from the credential the dead subscriber was
    STARTED with. Sampling the watcher's cache (one poll behind) would make
    any between-poll write look like a re-bind."""
    trigger = _BreakerTrigger()
    cred_at_start = _Credential(app_secret="broken")
    key = trigger._subscriber_key(cred_at_start)

    for _ in range(trigger.BREAKER_FAST_DEATH_THRESHOLD):
        _armed(trigger, cred_at_start)
        # The watcher's cache holds a DIFFERENT object with churned
        # bookkeeping — it must not be the fingerprint source.
        trigger._subscriber_creds[key] = _Credential(app_secret="broken", cursor="s_9")
        await trigger._breaker_record_death(key, trigger._subscriber_creds[key])

    assert trigger._breaker_cred_fingerprint[key] == trigger._credential_fingerprint(
        cred_at_start
    )
    assert await trigger._breaker_admit(key, cred_at_start) is False


@pytest.mark.asyncio
async def test_rebound_credential_clears_breaker():
    trigger = _BreakerTrigger()
    trigger._audit_repo = _CaptureAuditRepo()
    cred = _Credential(app_secret="")
    key = await _kill_until_tripped(trigger, cred)

    fixed = _Credential(app_secret="fresh-secret")
    assert await trigger._breaker_admit(key, fixed) is True
    assert key not in trigger._breaker_blocked_until
    reasons = [
        d.get("reason") for d in trigger._audit_repo.details_for(EVENT_SUBSCRIBER_BREAKER_CLEARED)
    ]
    assert reasons == ["credential_changed"]


@pytest.mark.asyncio
async def test_rebind_clear_keeps_escalation_memory():
    """A cleared breaker must not reset the backoff tier: if it did, a
    credential whose fingerprint churns for any reason we failed to exclude
    could be restarted at the shortest tier forever."""
    trigger = _BreakerTrigger()
    trigger.BREAKER_BACKOFF_SCHEDULE_SECONDS = (10.0, 900.0)
    cred = _Credential(app_secret="bad1")
    key = await _kill_until_tripped(trigger, cred)

    assert await trigger._breaker_admit(key, _Credential(app_secret="bad2")) is True
    await _kill_until_tripped(trigger, _Credential(app_secret="bad2"))

    assert trigger._breaker_blocked_until[key] - time.monotonic() > 100


# ── Lifecycle: state must not outlive what it describes ───────────────────

@pytest.mark.asyncio
async def test_removed_credential_purges_every_breaker_dict():
    trigger = _BreakerTrigger()
    cred = _Credential()
    key = await _kill_until_tripped(trigger, cred)
    assert key in trigger._breaker_blocked_until

    trigger._breaker_purge_stale(set())

    for name in (
        "_breaker_fast_deaths",
        "_breaker_trips",
        "_breaker_blocked_until",
        "_breaker_cred_fingerprint",
        "_unstartable_fingerprint",
    ):
        assert key not in getattr(trigger, name), name


@pytest.mark.asyncio
async def test_stop_clears_breaker_state(db_client):
    trigger = _BreakerTrigger()
    cred = _Credential()
    key = await _kill_until_tripped(trigger, cred)
    await trigger.start(db_client)
    await trigger.stop()

    assert key not in trigger._breaker_blocked_until
    assert key not in trigger._breaker_trips
    assert key not in trigger._subscriber_start_fingerprint


# ── Pre-flight gate: never start what provably cannot connect ─────────────

@pytest.mark.asyncio
async def test_unstartable_credential_is_never_started_and_audited_once():
    trigger = _BreakerTrigger()
    trigger._audit_repo = _CaptureAuditRepo()
    trigger.startable = False
    cred = _Credential(app_secret="")
    key = trigger._subscriber_key(cred)

    for _ in range(5):
        await trigger._note_unstartable(key, cred)

    assert trigger.loop_starts == 0
    assert trigger._audit_repo.types().count(EVENT_SUBSCRIBER_UNSTARTABLE) == 1

    # A re-bind is a new situation and must be reported again.
    await trigger._note_unstartable(key, _Credential(app_secret="fresh"))
    assert trigger._audit_repo.types().count(EVENT_SUBSCRIBER_UNSTARTABLE) == 2


@pytest.mark.asyncio
async def test_lark_pre_flight_rejects_cleared_secret():
    """The exact prod condition: App Secret cleared → zero restarts."""
    from xyz_agent_context.module.lark_module._lark_credential_manager import (
        LarkCredential,
    )
    from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger

    trigger = LarkTrigger()
    base = dict(
        agent_id="agent_x", app_id="cli_x", app_secret_ref="appsecret:cli_x",
        brand="lark", profile_name="agent_x",
    )
    assert trigger.should_start_subscriber(
        LarkCredential(**base, app_secret_encoded="")
    ) is False
    assert trigger.should_start_subscriber(
        LarkCredential(**base, app_secret_encoded="ZW5jb2RlZA==")
    ) is True


@pytest.mark.asyncio
async def test_unfingerprintable_credential_keeps_isolation():
    """A credential we cannot introspect must not read as "changed" on every
    poll — that would clear the breaker forever. Fail-safe direction: hold
    the isolation and let the backoff re-probe do the recovering."""
    trigger = _BreakerTrigger()

    class _Opaque:
        __slots__ = ("agent_id", "app_id")

        def __init__(self):
            self.agent_id = "agent_a"
            self.app_id = "bot1"

    first, second = _Opaque(), _Opaque()
    assert trigger._credential_fingerprint(first) == trigger._credential_fingerprint(
        second
    )

    key = "bot1"
    trigger._subscriber_started_at[key] = time.monotonic()
    trigger._subscriber_start_fingerprint[key] = trigger._credential_fingerprint(first)
    trigger._breaker_fast_deaths[key] = trigger.BREAKER_FAST_DEATH_THRESHOLD - 1
    await trigger._breaker_record_death(key, first)

    assert await trigger._breaker_admit(key, second) is False


@pytest.mark.asyncio
async def test_startable_again_clears_the_unstartable_mark_while_isolated(db_client):
    """The two gates are independent: a credential can be fixed (startable)
    while the breaker still holds it. The stale "unstartable" mark must not
    linger on the health surface."""
    trigger = _BreakerTrigger()
    cred = _Credential()
    key = await _kill_until_tripped(trigger, cred)
    trigger.startable = False
    await trigger._note_unstartable(key, cred)
    assert key in trigger._unstartable_fingerprint

    trigger.startable = True
    trigger.creds = [cred]
    await trigger.start(db_client)
    try:
        assert await _wait_until(lambda: key not in trigger._unstartable_fingerprint)
        assert key in trigger._breaker_blocked_until  # still isolated
    finally:
        await trigger.stop()


@pytest.mark.asyncio
async def test_matrix_excludes_since_token_from_fingerprint():
    from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
        MatrixTrigger,
    )

    assert "matrix_since_token" in MatrixTrigger.BREAKER_VOLATILE_CREDENTIAL_FIELDS


# ── Health surface: an isolated key must be visible without a DB query ────

@pytest.mark.asyncio
async def test_health_snapshot_explains_the_missing_subscriber():
    trigger = _BreakerTrigger()
    trigger._audit_repo = _CaptureAuditRepo()
    cred = _Credential()
    key = await _kill_until_tripped(trigger, cred)
    # The watcher keeps refreshing the credential cache for blocked keys.
    trigger._subscriber_creds[key] = cred

    snap = await _snapshot_one("fake", trigger)

    assert snap["subscriber_count"] == 0
    assert snap["subscriber_keys"] == [key]
    assert snap["breaker_isolated_keys"] == [key]
    assert snap["unstartable_keys"] == []


@pytest.mark.asyncio
async def test_heartbeat_carries_isolation_counts():
    trigger = _BreakerTrigger()
    trigger._audit_repo = _CaptureAuditRepo()
    cred = _Credential()
    await _kill_until_tripped(trigger, cred)
    trigger._unstartable_fingerprint["bot2"] = "fp"

    trigger._last_heartbeat_monotonic = 0.0
    await trigger._maybe_heartbeat()

    details = trigger._audit_repo.details_for(EVENT_HEARTBEAT)[-1]
    assert details["breaker_isolated_count"] == 1
    assert details["unstartable_count"] == 1


# ── Wiring: the watcher actually consults both gates ──────────────────────

async def _wait_until(predicate, timeout=4.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


@pytest.mark.asyncio
async def test_watcher_stops_restarting_once_tripped(db_client):
    trigger = _BreakerTrigger()
    await trigger.start(db_client)
    try:
        assert await _wait_until(lambda: "bot1" in trigger._breaker_blocked_until)
        settled = trigger.loop_starts
        # Isolation is 9999s: no re-probe can legitimately happen here.
        await asyncio.sleep(0.4)  # ~8 poll intervals
        assert trigger.loop_starts == settled == trigger.BREAKER_FAST_DEATH_THRESHOLD
    finally:
        await trigger.stop()


@pytest.mark.asyncio
async def test_watcher_never_starts_an_unstartable_credential(db_client):
    trigger = _BreakerTrigger()
    trigger.startable = False
    await trigger.start(db_client)
    try:
        await asyncio.sleep(0.4)  # ~8 poll intervals
        assert trigger.loop_starts == 0
        assert "bot1" not in trigger._breaker_blocked_until
        assert "bot1" in trigger._unstartable_fingerprint
    finally:
        await trigger.stop()
