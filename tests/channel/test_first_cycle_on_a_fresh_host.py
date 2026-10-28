"""
@file_name: test_first_cycle_on_a_fresh_host.py
@author: NarraNexus
@date: 2026-08-17
@description: "Never ran yet" must not be spelled 0.0 against a monotonic clock.

`time.monotonic()` counts from BOOT on Linux, so a `_last_*_monotonic = 0.0`
sentinel does not mean "never ran" — it means "ran at boot". Every gate of the
form `monotonic() - mark >= interval` therefore stays silently shut until the
HOST has been up longer than the interval:

  * channel-trigger heartbeat (600s) — no L2 liveness row for the first ten
    minutes of a freshly booted host, which is exactly the window where a
    failing start is most likely and exactly what incident lesson #4 wants
    those beats to cover.
  * channel-trigger retention sweep (24h) — `_run_cleanup` documents itself as
    "once at startup + daily"; a new EC2 instance got neither until its uptime
    reached a day.
  * `ServiceAuditor.heartbeat` (60s) — same shape, small window.

None of this is visible on a developer laptop or a long-lived EC2, where uptime
is measured in weeks: the gate always looks open. It surfaced from the other
end — `test_credential_breaker.py::test_heartbeat_carries_isolation_counts`
used the same `0.0` idiom and passed everywhere except on a fresh CI runner,
where it indexed an empty list (2026-08-17). The test was one instance; the
three production marks were the same assumption.

**These tests drive the gates rather than reading the source.** An earlier draft
asserted `'= float("-inf")' in inspect.getsource(...)`, which fails on a
semantically identical rewrite (extract the sentinel to a constant and it goes
red for nothing) and — worse — stays GREEN if somebody changes the gate itself
to seed semantics (`if mark == -inf: mark = now`), which is the one edit the
mirror doc explicitly warns against. It also claimed a behaviour test needed "a
live queue, workers and an audit repo"; `_maybe_heartbeat` needs only the audit
repo, and `test_credential_breaker.py` had been doing exactly this for a whole
test in the same directory.

The interval is made enormous instead of faking the clock. `-inf` clears any
finite interval; a `0.0` mark cannot clear 1e12 seconds on any real host. That
keeps the discriminating power and drops a fixture that moved `time.monotonic`
BACKWARDS process-wide — harmless only for as long as nobody adds an `await
asyncio.sleep(...)` here, since `loop.time()` IS `time.monotonic()` and a
rewound clock makes timers hang instead of fire.
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.platform.channel.channel_audit_events import EVENT_HEARTBEAT
from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase
from narranexus.platform.schema.hook_schema import WorkingSource
from narranexus.platform.services.service_audit import ServiceAuditor


# Seven orders of magnitude past any plausible host uptime, so "the gate opened"
# can only mean the sentinel cleared it — never "this machine happens to have
# been up a while". An int because what it expresses is an order of magnitude of
# seconds with no fractional meaning — unlike the poll interval below, which is
# deliberately fractional. Neither spelling is a type error under the current
# pyright config (`typeCheckingMode: off`, `include` limited to
# `src/narranexus/platform/module_system`, `reportAssignmentType` not promoted), so this
# is a readability convention, not a lint fix.
_UNREACHABLE_INTERVAL = 10**12


class _CaptureAuditRepo:
    """Records what the trigger writes. Same shape as the one in
    `test_credential_breaker.py`; deliberately re-declared rather than imported,
    so the two files do not get welded together by a stub that exists to serve
    the other one's subscriber semantics."""

    def __init__(self):
        self.rows: list = []

    async def append(self, event_type, **kwargs):
        self.rows.append((event_type, kwargs))

    def types(self) -> list:
        return [t for t, _ in self.rows]


class _OneCredentialTrigger(ChannelTriggerBase):
    """The thinnest concrete trigger that can complete one watcher pass.

    It must report exactly ONE credential, not zero: with nothing active
    `_credential_watcher` takes its idle branch — sleep, `continue` — and never
    reaches the two gates at the end of the body. Starting the subscriber is
    then short-circuited, so no transport, no workers and no queue are involved.
    """

    channel_name = "fresh-host-probe"
    brand_display = "FreshHostProbe"
    working_source = WorkingSource.LARK

    # Both gates are unreachable by elapsed time; only the sentinel can open
    # them. Overridden on the SUBCLASS: patching the base class would leak into
    # every other channel test in the session.
    HEARTBEAT_INTERVAL_SECONDS = _UNREACHABLE_INTERVAL
    CLEANUP_INTERVAL_SECONDS = _UNREACHABLE_INTERVAL
    # Small but non-zero: one pass is all this runs, and a 0 here would turn any
    # future regression that stops the loop terminating into a hot spin instead
    # of a wait. No IDLE_POLL override — with one credential the idle branch is
    # unreachable, and pinning a value nothing reads reads like coverage.
    CREDENTIAL_POLL_INTERVAL_SECONDS = 0.01

    def __init__(self):
        super().__init__()
        self.cleanup_calls = 0
        self.passes = 0

    # ── abstract surface, all inert ──────────────────────────────────────
    async def load_active_credentials(self):
        # Termination lives HERE, deliberately, and not in a gate.
        #
        # It used to live in the stubbed `_run_cleanup`, which made the exit
        # condition the very thing under test: if the cleanup gate regressed,
        # the loop never ended, `wait_for` cancelled it, and the only report was
        # a bare TimeoutError — while `assert cleanup_calls == 1` could not fail
        # at all, because it was never reached unless cleanup had already run.
        # A tautology guarding a gate, reporting a timeout. Measured, not
        # theorised: seed-semantics on the cleanup gate produced
        # `E TimeoutError` with none of the authored text.
        #
        # `while self.running` is re-read only at the top of the body, so
        # clearing it here still lets THIS pass finish both gates.
        self.passes += 1
        self.running = False
        return [object()]

    def _subscriber_key(self, credential) -> str:
        return "the-one-key"

    async def _maybe_start_subscriber(self, key, cred) -> None:
        return  # no transport in a unit test

    async def connect(self, credential):  # pragma: no cover — never reached
        raise AssertionError(
            "_maybe_start_subscriber is stubbed out, so no transport is opened; "
            "reaching connect() means that override was removed"
        )

    def parse_event(self, raw):  # pragma: no cover
        return None

    async def is_echo(self, message, credential) -> bool:  # pragma: no cover
        return False

    async def resolve_sender_name(self, sender_id, credential) -> str:  # pragma: no cover
        return ""

    def create_context_builder(self, credential):  # pragma: no cover
        return None

    # ── seams ────────────────────────────────────────────────────────────
    def _desired_worker_count(self) -> int:
        return 0  # never spawn real workers

    async def _run_cleanup(self) -> None:
        """Counts, and nothing else.

        Not because the real sweep would fail here — it short-circuits on
        `self._dedup_store is not None` and would run fine — but because "did
        the gate open" is the whole question, and a counter answers it without
        dragging the sweep's own behaviour into the verdict. It also writes no
        mark: the loop ends after this pass, so maintaining one would be a dead
        write suggesting multi-pass bookkeeping that does not exist.
        """
        self.cleanup_calls += 1


async def _one_watcher_pass(trigger: _OneCredentialTrigger) -> None:
    trigger.running = True
    try:
        await asyncio.wait_for(trigger._credential_watcher(), timeout=5)
    except TimeoutError:  # pragma: no cover — the regression path
        # A timeout here means the loop never came back, and the bare
        # `TimeoutError` asyncio raises points at `asyncio/timeouts.py`. Dump
        # what the pass actually managed, or the next reader concludes "flaky
        # async test" and relaxes the guard — which is how guards die.
        pytest.fail(
            "the credential watcher never completed one pass: "
            f"passes={trigger.passes}, cleanup_calls={trigger.cleanup_calls}, "
            f"audit={trigger._audit_repo.types()}, "
            f"marks=(cleanup={trigger._last_cleanup_monotonic}, "
            f"heartbeat={trigger._last_heartbeat_monotonic})"
        )


def test_the_marks_are_a_real_never_ran_sentinel():
    """The value, not its spelling.

    Extracting the sentinel into a named constant must keep this green;
    regressing to 0.0 must turn it red. `inspect.getsource` gets both backwards.
    """
    trigger = _OneCredentialTrigger()

    assert trigger._last_heartbeat_monotonic == float("-inf")
    assert trigger._last_cleanup_monotonic == float("-inf")
    assert ServiceAuditor("svc")._last_heartbeat_at == float("-inf"), (
        "0.0 is not 'never beat' against a monotonic clock — it is 'beat at boot'"
    )


@pytest.mark.asyncio
async def test_the_first_watcher_pass_beats_and_sweeps():
    """Drive the gates. This is the half a source-text assertion cannot do:
    it fails if somebody rewrites either gate into seed semantics, which is the
    edit that brings the original bug back with the initial values intact.
    """
    trigger = _OneCredentialTrigger()
    trigger._audit_repo = _CaptureAuditRepo()

    await _one_watcher_pass(trigger)

    assert trigger.passes == 1, "the watcher body ran a number of times it should not"
    assert trigger.cleanup_calls == 1, (
        "the retention sweep did not run on the first pass — a new host would "
        "wait for its uptime to reach CLEANUP_INTERVAL_SECONDS"
    )
    assert EVENT_HEARTBEAT in trigger._audit_repo.types(), (
        "no heartbeat on the first pass — the L2 liveness signal is missing "
        "during the window a bad start is most likely (incident lesson #4)"
    )


@pytest.mark.asyncio
async def test_a_service_auditor_beats_on_its_very_first_call():
    """A finite gate must not depend on the host being older than it."""
    auditor = ServiceAuditor("svc-under-test", heartbeat_interval=_UNREACHABLE_INTERVAL)
    emitted: list = []

    async def _capture(event, detail):
        emitted.append(event)

    auditor._emit = _capture

    await auditor.heartbeat()

    assert emitted, (
        "the first heartbeat was skipped on a host younger than the interval — "
        "no liveness signal during the window a bad start is most likely"
    )
