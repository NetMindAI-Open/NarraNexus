"""
@file_name: test_executor_reaper.py
@date: 2026-06-17
@description: Idle-cull reaper — stops idle executors, skips failures,
no-op without a broker, and never culls a user whose run is live in another
process. Pure coordinator, tested via DI fakes.
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.platform.agent_runtime.executor_reaper import (
    UNKNOWN_RUN,
    _REAPER_LIVENESS,
    ExecutorReaper,
    _CullVeto,
    live_run_elsewhere,
    maybe_start_executor_reaper,
    no_live_recorded_run_for,
)


class _FakeController:
    """Returns a fixed idle set once, then empty (mimics claim semantics).

    Applies the injected veto the way the real controller does — vetoed users
    are withheld AND keep their stamp, so they are re-offered next pass.
    """

    def __init__(self, idle):
        self._idle = list(idle)
        self.restamped: list[str] = []
        self.per_check_budget: float = 0.0

    async def claim_idle_users(self, ttl_seconds, is_busy=None,
                               per_check_budget=0.0):
        self.per_check_budget = per_check_budget
        users, self._idle = self._idle, []
        if is_busy is None:
            return users
        kept, claimed = [], []
        for user_id in users:
            (kept if await is_busy(user_id) else claimed).append(user_id)
        self._idle = kept
        return claimed

    async def restamp_idle(self, user_id):
        self.restamped.append(user_id)


@pytest.fixture
def audited(monkeypatch):
    """Capture cull-skip audit rows instead of reaching for a DB client."""
    rows: list[tuple[str, str]] = []

    async def fake_audit(event_type, **kw):
        rows.append((kw.get("user_id"), kw.get("run_id")))
        return True          # the row landed; a falsy return means "retry"

    monkeypatch.setattr(
        "narranexus.platform.agent_runtime.executor_reaper._audit", fake_audit
    )
    return rows


@pytest.mark.asyncio
async def test_reap_once_stops_all_idle_users():
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)
        return True

    reaper = ExecutorReaper(
        _FakeController(["a", "b"]), stop_fn, is_busy=None, ttl_seconds=1
    )
    reaped = await reaper.reap_once()
    assert reaped == ["a", "b"]
    assert stopped == ["a", "b"]


@pytest.mark.asyncio
async def test_reap_once_skips_stop_failures():
    async def stop_fn(user_id):
        if user_id == "b":
            raise RuntimeError("broker down")
        return True

    controller = _FakeController(["a", "b", "c"])
    reaper = ExecutorReaper(controller, stop_fn, is_busy=None, ttl_seconds=1)
    reaped = await reaper.reap_once()
    assert reaped == ["a", "c"]   # b failed → skipped, pass not aborted
    # Claimed but not stopped ⇒ the stamp goes back, or b's container is
    # never reclaimed. stop_fn is an HTTP call to the broker, so this path
    # runs on every deploy restart and every 5xx.
    assert controller.restamped == ["b"]


@pytest.mark.asyncio
async def test_reap_once_empty_when_nothing_idle():
    reaper = ExecutorReaper(
        _FakeController([]), lambda u: None, is_busy=None, ttl_seconds=1
    )
    assert await reaper.reap_once() == []


@pytest.mark.asyncio
async def test_a_stop_failure_does_not_abort_the_pass():
    """One user's broker hiccup must not stop the others from being culled —
    the broker's own label-based reaper is the backstop for the one that
    failed."""
    stopped = []

    async def stop_fn(user_id):
        if user_id == "b":
            raise RuntimeError("broker down")
        stopped.append(user_id)
        return True

    reaper = ExecutorReaper(
        _FakeController(["a", "b", "c"]), stop_fn, is_busy=None, ttl_seconds=1
    )
    reaped = await reaper.reap_once()

    assert stopped == ["a", "c"]
    assert reaped == ["a", "c"]


def test_maybe_start_is_noop_without_broker(monkeypatch):
    monkeypatch.delenv("BROKER_URL", raising=False)
    assert maybe_start_executor_reaper() is None


# --------------------------------------------------------------------------
# Cross-process liveness — the 2026-07-31 incident
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_busy_in_another_process_is_not_reaped():
    """The incident itself: idle here (web chat ended 20min ago), live in
    workers (group-chat reply in flight)."""
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)
        return True

    async def is_busy(user_id):
        return user_id == "busy-elsewhere"

    controller = _FakeController(["idle", "busy-elsewhere"])
    reaper = ExecutorReaper(controller, stop_fn, is_busy=is_busy, ttl_seconds=1)

    assert await reaper.reap_once() == ["idle"]
    assert stopped == ["idle"]


@pytest.mark.asyncio
async def test_a_vetoed_user_keeps_its_stamp_and_is_reoffered():
    """Withholding must not double as forgetting — otherwise the fix trades
    the kill for a container that never gets reaped at all."""
    stopped = []

    async def stop_fn(user_id):
        stopped.append(user_id)
        return True

    busy = {"u"}

    async def is_busy(user_id):
        return user_id in busy

    controller = _FakeController(["u"])
    reaper = ExecutorReaper(controller, stop_fn, is_busy=is_busy, ttl_seconds=1)

    assert await reaper.reap_once() == []      # run still live
    busy.clear()                                # run finished
    assert await reaper.reap_once() == ["u"]    # re-offered, now culled
    assert stopped == ["u"]


@pytest.mark.asyncio
async def test_user_that_became_busy_between_claim_and_stop_is_restamped():
    """Stops are sequential and `docker stop` waits out a SIGTERM grace, so a
    verdict taken at claim time can be minutes stale by the time this user's
    turn comes."""
    stopped = []
    calls = {"n": 0}

    async def stop_fn(user_id):
        stopped.append(user_id)
        return True

    async def is_busy(user_id):
        calls["n"] += 1
        return calls["n"] > 1   # idle at claim time, busy at stop time

    controller = _FakeController(["u"])
    reaper = ExecutorReaper(controller, stop_fn, is_busy=is_busy, ttl_seconds=1)

    assert await reaper.reap_once() == []
    assert stopped == []
    assert controller.restamped == ["u"]   # not dropped from idle tracking


@pytest.mark.asyncio
async def test_veto_dedupes_audit_per_run_not_per_pass(audited):
    """A 10-hour agent (rule #14: normal) blocks ~300 passes. The metric
    counts runs saved, so it must write one row, not 300."""
    async def check(user_id):
        return "evt_long"

    veto = _CullVeto(check=check)
    for _ in range(5):
        assert await veto("u") is True

    assert audited == [("u", "evt_long")]
    assert veto._blocked_by == {"u": "evt_long"}


@pytest.mark.asyncio
async def test_veto_re_audits_when_a_different_run_blocks(audited):
    seen = ["evt_one", "evt_one", "evt_two"]

    async def check(user_id):
        return seen.pop(0)

    veto = _CullVeto(check=check)
    await veto("u")
    await veto("u")
    await veto("u")
    assert veto._blocked_by == {"u": "evt_two"}


@pytest.mark.asyncio
async def test_veto_forgets_a_user_that_came_back_clean(audited):
    answers = ["evt_x", None]

    async def check(user_id):
        return answers.pop(0)

    veto = _CullVeto(check=check)
    assert await veto("u") is True
    assert await veto("u") is False
    assert veto._blocked_by == {}


@pytest.mark.asyncio
async def test_veto_tracking_is_bounded(audited):
    async def check(user_id):
        return f"evt_{user_id}"

    veto = _CullVeto(check=check)
    for i in range(_CullVeto._MAX_TRACKED + 50):
        await veto(f"u{i}")
    assert len(veto._blocked_by) == _CullVeto._MAX_TRACKED


@pytest.mark.asyncio
async def test_unreadable_db_reads_as_busy(monkeypatch):
    """Not knowing must never authorise a stop (rule #14)."""
    async def boom():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", boom
    )
    assert await live_run_elsewhere(
        "u", caller="reaper", consequence="culling is OFF"
    ) == UNKNOWN_RUN


@pytest.mark.asyncio
async def test_recording_kill_switch_disables_culling(monkeypatch):
    """The switch turns off the very rows this guard reads, so it must not
    silently become a licence to destroy containers."""
    from narranexus.platform.agent_runtime.run_recorder import RECORDING_DISABLED_ENV

    monkeypatch.setenv(RECORDING_DISABLED_ENV, "1")
    assert await live_run_elsewhere(
        "u", caller="reaper", consequence="culling is OFF"
    ) == UNKNOWN_RUN


@pytest.mark.asyncio
async def test_unknown_verdicts_are_not_audited(audited):
    """Rows must count runs actually saved; 'we could not tell' is a log and
    an alert, not a near-miss."""
    async def unknown(user_id):
        return UNKNOWN_RUN

    assert await _CullVeto(check=unknown)("u") is True
    assert audited == []

    async def real(user_id):
        return "evt_real"

    assert await _CullVeto(check=real)("u") is True
    assert audited == [("u", "evt_real")]


def test_production_wiring_installs_the_cross_process_veto(monkeypatch):
    """The guard is only real if the production factory installs it. Without
    this, a reaper constructed with is_busy=None would silently restore the
    pre-2026-07-31 behaviour and every other test here would still pass."""
    import asyncio

    import narranexus.platform.agent_runtime.executor_reaper as mod

    monkeypatch.setattr(
        "narranexus.platform.agent_framework.loop.broker_client.broker_url",
        lambda: "http://broker:8030",
    )
    built = {}

    class _Spy(mod.ExecutorReaper):
        def __init__(self, *a, **kw):
            built.update(kw)
            super().__init__(*a, **kw)

        async def run_forever(self):
            return None

    monkeypatch.setattr(mod, "ExecutorReaper", _Spy)

    async def _go():
        task = mod.maybe_start_executor_reaper()
        assert task is not None
        await task

    asyncio.run(_go())
    assert isinstance(built.get("is_busy"), mod._CullVeto)


@pytest.mark.asyncio
async def test_a_failing_restamp_does_not_abort_the_pass():
    """The handler's contract is that one user's failure never aborts the
    pass; that must not depend on what a collaborator does later."""
    class _BrokenRestamp(_FakeController):
        async def restamp_idle(self, user_id):
            raise RuntimeError("controller wedged")

    stopped = []

    async def stop_fn(user_id):
        if user_id == "b":
            raise RuntimeError("broker down")
        stopped.append(user_id)
        return True

    reaper = ExecutorReaper(
        _BrokenRestamp(["a", "b", "c"]), stop_fn, is_busy=None, ttl_seconds=1
    )
    assert await reaper.reap_once() == ["a", "c"]
    assert stopped == ["a", "c"]


# --------------------------------------------------------------------------
# Blind-pass reporting — "culling stopped" vs "nothing to cull"
# --------------------------------------------------------------------------


async def _noop_stop(user_id):
    return True


@pytest.fixture(autouse=True)
def _reset_reaper_module_state():
    import narranexus.platform.agent_runtime.executor_reaper as mod

    def _clear():
        # Every mutable module-level name this file can write. _STALE_AFTER_S
        # used to belong here too; it is now carried inside _LAST_PASS, which
        # is one fewer way for a test to leak into the next one.
        mod._LAST_PASS = None
        mod._LAST_PASS_AT = None
        mod._TASK_ERROR = None
        mod._recording_off_warned.clear()

    _clear()
    yield
    _clear()


@pytest.fixture
def audit_rows(monkeypatch):
    rows = []

    async def fake_audit(event_type, **kw):
        rows.append((event_type, kw.get("detail")))
        return True          # the row landed; a falsy return means "retry"

    monkeypatch.setattr(
        "narranexus.platform.agent_runtime.executor_reaper._audit", fake_audit
    )
    return rows


def test_status_reports_not_running_before_any_pass():
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    status = reaper_status()
    assert status["running"] is False
    assert status["stale"] is None
    assert status["veto_installed"] is None   # unknown, NOT "no guard"
    assert status["task_error"] is None
    # Same key set as a reported pass. This branch covers every process's
    # first interval after a deploy — precisely when someone is watching —
    # so a consumer indexing these must not KeyError.
    for key in ("age_seconds", "reaped", "blind_passes", "judged", "vetoed",
                "blind", "recheck_judged", "recheck_vetoed"):
        assert key in status


@pytest.mark.asyncio
async def test_a_wholly_blind_pass_is_counted_and_audited(audit_rows):
    """The failure this exists for: every candidate unreadable ⇒ nothing is
    ever culled, and from outside it looks exactly like an idle system."""
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    async def blind(user_id):
        return UNKNOWN_RUN

    reaper = ExecutorReaper(
        _FakeController(["a", "b"]), _noop_stop,
        is_busy=_CullVeto(check=blind), ttl_seconds=1,
    )
    assert await reaper.reap_once() == []

    status = reaper_status()
    assert status["running"] is True
    assert status["blind_passes"] == 1
    assert status["judged"] == 2 and status["blind"] == 2
    assert [r[0] for r in audit_rows] == ["cull_disabled"]
    assert audit_rows[0][1]["judged"] == 2


@pytest.mark.asyncio
async def test_a_real_veto_is_not_a_blind_pass(audit_rows):
    """A live run blocking the cull is the guard WORKING; it must not read as
    the reaper having gone blind."""
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    async def busy(user_id):
        return "evt_real"

    reaper = ExecutorReaper(
        _FakeController(["a"]), _noop_stop,
        is_busy=_CullVeto(check=busy), ttl_seconds=1,
    )
    await reaper.reap_once()
    assert reaper_status()["blind_passes"] == 0
    assert [r[0] for r in audit_rows] == ["cull_skipped_busy"]


@pytest.mark.asyncio
async def test_blind_passes_reset_once_liveness_is_readable_again(audit_rows):
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    # reap_once asks twice per surviving user (claim, then again before the
    # stop), so this is keyed on the pass, not on a call counter.
    blind = {"still": True}

    async def check(user_id):
        return UNKNOWN_RUN if blind["still"] else None

    controller = _FakeController(["a"])
    reaper = ExecutorReaper(
        controller, _noop_stop, is_busy=_CullVeto(check=check), ttl_seconds=1
    )

    await reaper.reap_once()
    assert reaper_status()["blind_passes"] == 1
    blind["still"] = False
    controller._idle = ["a"]
    assert await reaper.reap_once() == ["a"]
    assert reaper_status()["blind_passes"] == 0


@pytest.mark.asyncio
async def test_an_empty_pass_is_not_blind(audit_rows):
    """Nothing due is the healthy steady state — it must not raise the alarm
    the blind counter exists to raise."""
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    async def check(user_id):
        return None

    reaper = ExecutorReaper(
        _FakeController([]), _noop_stop,
        is_busy=_CullVeto(check=check), ttl_seconds=1,
    )
    await reaper.reap_once()
    assert reaper_status()["blind_passes"] == 0
    assert audit_rows == []


@pytest.mark.asyncio
async def test_a_slow_liveness_lookup_counts_as_blind(audit_rows, monkeypatch):
    """The common DB degradation is SLOW, not dead. Bounding the lookup only
    at the admission layer cancels the veto mid-await, so nothing is tallied
    and the pass reports the same zeros as a healthy empty one — the exact
    blind spot the blind-pass alarm exists to close."""
    import narranexus.platform.agent_runtime.executor_reaper as mod
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    monkeypatch.setattr(mod, "_PER_CANDIDATE_S", 0.02)

    async def wedged(user_id):
        await asyncio.sleep(5)          # never returns within the budget
        return None

    reaper = ExecutorReaper(
        _FakeController(["a"]), _noop_stop,
        is_busy=_CullVeto(check=wedged), ttl_seconds=1,
    )
    assert await reaper.reap_once() == []

    status = reaper_status()
    assert status["judged"] == 1 and status["blind"] == 1
    assert status["blind_passes"] == 1
    assert [r[0] for r in audit_rows] == ["cull_disabled"]


@pytest.mark.asyncio
async def test_a_pass_with_no_candidates_does_not_reset_blind_passes(audit_rows):
    """The kill switch stays on for hours; an idle minute in the middle must
    not punch the counter back to zero and defeat a threshold alert."""
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    async def blind(user_id):
        return UNKNOWN_RUN

    veto = _CullVeto(check=blind)
    controller = _FakeController(["a"])
    reaper = ExecutorReaper(controller, _noop_stop, is_busy=veto, ttl_seconds=1)

    await reaper.reap_once()
    assert reaper_status()["blind_passes"] == 1
    controller._idle = []                    # nobody due this pass
    await reaper.reap_once()
    assert reaper_status()["blind_passes"] == 1   # unchanged, not reset


@pytest.mark.asyncio
async def test_claim_and_recheck_are_counted_separately(audit_rows):
    """One healthy pass asks twice per survivor. Merged, `judged: 10,
    reaped: 5` sends the reader hunting for 5 users that do not exist."""
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    async def idle(user_id):
        return None

    reaper = ExecutorReaper(
        _FakeController(["a", "b"]), _noop_stop,
        is_busy=_CullVeto(check=idle), ttl_seconds=1,
    )
    assert await reaper.reap_once() == ["a", "b"]

    status = reaper_status()
    assert status["judged"] == 2            # candidates, not questions
    assert status["recheck_judged"] == 2
    assert status["reaped"] == 2


@pytest.mark.asyncio
async def test_a_blind_recheck_is_not_a_blind_pass(audit_rows):
    """Claim phase read the DB fine; a hiccup during the recheck is a hiccup,
    not a reaper that has gone blind."""
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    seen = {"n": 0}

    async def check(user_id):
        seen["n"] += 1
        return None if seen["n"] == 1 else UNKNOWN_RUN

    reaper = ExecutorReaper(
        _FakeController(["a"]), _noop_stop,
        is_busy=_CullVeto(check=check), ttl_seconds=1,
    )
    assert await reaper.reap_once() == []          # recheck withheld the stop
    status = reaper_status()
    assert status["blind_passes"] == 0
    assert status["recheck_vetoed"] == 1
    assert [r[0] for r in audit_rows] == []        # no cull_disabled


@pytest.mark.asyncio
async def test_a_reaper_without_the_veto_is_reported_not_hidden(audit_rows):
    """is_busy=None is the pre-2026-07-31 configuration: it culls on this
    process's local view alone. Reporting it as "not running" would send the
    reader looking for a leak while runs are being cut off."""
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    reaper = ExecutorReaper(
        _FakeController(["a"]), _noop_stop, is_busy=None, ttl_seconds=1
    )
    await reaper.reap_once()

    status = reaper_status()
    assert status["running"] is True
    assert status["veto_installed"] is False
    # Explicit zeros, not absent keys — a watcher reading body["judged"]
    # should get a number, not a KeyError.
    for key in ("judged", "vetoed", "blind", "recheck_judged", "recheck_vetoed"):
        assert status[key] == 0


@pytest.mark.asyncio
async def test_status_goes_stale_when_no_pass_completes(audit_rows):
    """"The task exists" is L1 and proves nothing (incident lesson #4): a
    wedged reaper keeps reporting its last good pass forever."""
    import narranexus.platform.agent_runtime.executor_reaper as mod
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    async def idle(user_id):
        return None

    reaper = ExecutorReaper(
        _FakeController([]), _noop_stop,
        is_busy=_CullVeto(check=idle), ttl_seconds=1, interval_seconds=10,
    )
    await reaper.reap_once()
    assert reaper_status()["stale"] is False

    mod._LAST_PASS_AT -= 31        # > 3 intervals with no completed pass
    status = reaper_status()
    assert status["stale"] is True
    assert status["age_seconds"] >= 31


def test_a_dead_background_task_is_visible_in_status():
    """Its only other trace is one log line the next rotation eats."""
    import narranexus.platform.agent_runtime.executor_reaper as mod
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    async def _boom():
        raise RuntimeError("reap loop exploded")

    async def _run():
        task = asyncio.get_running_loop().create_task(_boom())
        task.add_done_callback(mod._on_reaper_done)
        with pytest.raises(RuntimeError):
            await task

    asyncio.run(_run())
    assert "reap loop exploded" in (reaper_status()["task_error"] or "")


@pytest.mark.asyncio
async def test_cull_disabled_rows_are_rate_limited_like_the_warning(
    audit_rows, monkeypatch
):
    """One row per pass would make the row count a function of outage
    duration — the shape this file already avoids for run duration."""
    import narranexus.platform.agent_runtime.executor_reaper as mod

    monkeypatch.setattr(mod, "_BLIND_WARN_EVERY", 3)

    async def blind(user_id):
        return UNKNOWN_RUN

    veto = _CullVeto(check=blind)
    controller = _FakeController(["a"])
    reaper = ExecutorReaper(controller, _noop_stop, is_busy=veto, ttl_seconds=1)

    for _ in range(6):
        controller._idle = ["a"]
        await reaper.reap_once()

    # Passes 1 and 4 write; 2/3/5/6 ride the same tick as the warning.
    assert [r[0] for r in audit_rows] == ["cull_disabled", "cull_disabled"]
    # The cause travels in the row so the reader does not have to guess.
    assert audit_rows[0][1]["recording_disabled"] is False


@pytest.mark.asyncio
async def test_cull_disabled_names_the_kill_switch_when_that_is_the_cause(
    audit_rows, monkeypatch
):
    from narranexus.platform.agent_runtime.run_recorder import RECORDING_DISABLED_ENV

    monkeypatch.setenv(RECORDING_DISABLED_ENV, "1")
    controller = _FakeController(["a"])
    reaper = ExecutorReaper(
        controller, _noop_stop, is_busy=_CullVeto(), ttl_seconds=1
    )
    await reaper.reap_once()
    assert audit_rows[0][1]["recording_disabled"] is True


@pytest.mark.asyncio
async def test_status_key_set_is_identical_before_and_after_a_pass(audit_rows):
    """Two shapes for one endpoint section is how a watcher breaks in the
    window it is most needed."""
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    before = set(reaper_status())

    async def idle(user_id):
        return None

    reaper = ExecutorReaper(
        _FakeController(["a"]), _noop_stop,
        is_busy=_CullVeto(check=idle), ttl_seconds=1,
    )
    await reaper.reap_once()
    assert set(reaper_status()) == before


@pytest.mark.asyncio
async def test_the_two_timeout_layers_together_still_record_a_blind_pass(
    audit_rows, monkeypatch
):
    """The bug this guards was a LAYER bug: admission's batch wait_for
    cancelled the veto mid-await, so nothing was tallied and a wedged DB read
    as a healthy idle system. Every other test here runs against a fake
    controller with no outer budget at all, so the fix's actual precondition
    — the inner budget fires FIRST — is never exercised. Drive the real
    controller so a regression in either constant fails here.
    """
    import narranexus.platform.agent_runtime.admission as adm
    import narranexus.platform.agent_runtime.executor_reaper as mod
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    # With room for a whole check, not merely "smaller": a per-check budget
    # of 59 against a batch of 60 would satisfy `<` and still let the batch
    # cancel every candidate after the first.
    assert adm._VETO_BUDGET_S >= (mod._PER_CANDIDATE_S + mod._AUDIT_WRITE_S) * 2

    monkeypatch.setattr(mod, "_PER_CANDIDATE_S", 0.05)
    # Part of what reap_once reserves per call, so it has to shrink with it —
    # otherwise the reservation (0.05 + 5.0) exceeds the batch and the single
    # candidate is held back before any of this is exercised.
    monkeypatch.setattr(mod, "_AUDIT_WRITE_S", 0.05)
    monkeypatch.setattr(adm, "_VETO_BUDGET_S", 5.0)

    now = {"t": 0.0}
    controller = adm.AgentAdmissionController(
        None, None, None, 0, clock=lambda: now["t"]
    )
    await controller.release(await controller.acquire("u"))
    now["t"] = 100.0                                   # idle past the TTL

    async def wedged(user_id):
        await asyncio.sleep(30)                        # DB is slow, not dead
        return None

    reaper = ExecutorReaper(
        controller, _noop_stop, is_busy=_CullVeto(check=wedged), ttl_seconds=1
    )
    assert await reaper.reap_once() == []

    status = reaper_status()
    assert status["judged"] == 1 and status["blind"] == 1
    assert status["blind_passes"] == 1
    assert [r[0] for r in audit_rows] == ["cull_disabled"]
    # ...and the user keeps its stamp, so it is reconsidered next pass.
    assert await controller.claim_idle_users(1) == ["u"]


@pytest.mark.asyncio
async def test_the_batch_budget_never_cancels_a_check_mid_flight(monkeypatch):
    """A check cancelled by the OUTER budget vanishes from the tally — and it
    is the last candidate of every wedged pass, not an occasional one. The
    batch must decline to start what it cannot see through, leaving the
    candidate its stamp for the next pass."""
    import narranexus.platform.agent_runtime.admission as adm

    monkeypatch.setattr(adm, "_VETO_BUDGET_S", 10.0)

    now = {"t": 0.0}
    c = adm.AgentAdmissionController(None, None, None, 0, clock=lambda: now["t"])
    for user in ("a", "b"):
        await c.release(await c.acquire(user))
    now["t"] = 100.0

    started = []

    async def slow(user_id):
        started.append(user_id)
        now["t"] += 5.0            # half the batch budget per check
        return False

    claimed = await c.claim_idle_users(1, is_busy=slow, per_check_budget=8.0)

    # b is never STARTED: 5s of the 10s batch remained, less than one whole
    # check. The old code started it and let the outer timer kill it, which
    # is exactly the candidate that then went uncounted.
    assert started == ["a"]
    assert claimed == ["a"]
    assert await c.claim_idle_users(1) == ["b"]      # stamp intact


@pytest.mark.asyncio
async def test_a_wedged_audit_write_cannot_park_the_pass(monkeypatch):
    """The one code path both audit fixtures replace, so nothing else here
    touches it. Its promise: a stuck pool must not park the pass, because a
    pass that never finishes never reports — and a wedged reaper would then
    show up as "never ran"."""
    import narranexus.platform.agent_runtime.executor_reaper as mod

    monkeypatch.setattr(mod, "_AUDIT_WRITE_S", 0.02)

    async def wedged_client():
        await asyncio.sleep(30)

    # Patched on the SOURCE module: _audit imports it inside the function.
    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", wedged_client
    )

    # Returns, does not raise, and does not wait for the pool.
    await mod._audit("cull_disabled", detail={"judged": 1})


@pytest.mark.asyncio
async def test_a_wedged_audit_write_still_lets_the_pass_report(monkeypatch):
    """The same promise, stated the way the docstring states it: the pass
    completes and reaper_status() says so."""
    import narranexus.platform.agent_runtime.executor_reaper as mod
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    monkeypatch.setattr(mod, "_AUDIT_WRITE_S", 0.02)

    async def wedged_client():
        await asyncio.sleep(30)

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", wedged_client
    )

    async def blind(user_id):
        return UNKNOWN_RUN

    reaper = ExecutorReaper(
        _FakeController(["a"]), _noop_stop,
        is_busy=_CullVeto(check=blind), ttl_seconds=1,
    )
    assert await reaper.reap_once() == []

    status = reaper_status()
    assert status["running"] is True          # NOT "never ran"
    assert status["blind_passes"] == 1


@pytest.mark.asyncio
async def test_reap_once_hands_its_per_call_budget_to_the_batch(audit_rows):
    """A wiring test, like test_production_wiring_...: delete that one keyword
    argument and every behavioural test here still passes, because the guard
    test calls claim_idle_users directly and the fake controller swallows the
    parameter. Its default is 0.0 — i.e. "guard off" — so a lost line brings
    back "the last candidate of every wedged pass goes uncounted"."""
    import narranexus.platform.agent_runtime.executor_reaper as mod

    controller = _FakeController([])
    reaper = ExecutorReaper(
        controller, _noop_stop, is_busy=_CullVeto(), ttl_seconds=1
    )
    await reaper.reap_once()

    # Read off the module, not a literal: retuning either constant must not
    # turn this into noise.
    assert controller.per_check_budget == mod._PER_CANDIDATE_S + mod._AUDIT_WRITE_S


@pytest.mark.asyncio
async def test_a_lost_audit_row_is_retried_next_pass(monkeypatch):
    """These rows are the guard's only durable evidence — one per run saved.
    Noting "already audited" before the write means a pool stall drops that
    row forever: the next pass sees the same (user, run) and never retries."""
    import narranexus.platform.agent_runtime.executor_reaper as mod

    attempts = []

    async def flaky_audit(event_type, **kw):
        attempts.append(kw.get("run_id"))
        return len(attempts) > 1        # first write fails, second lands

    monkeypatch.setattr(mod, "_audit", flaky_audit)

    async def busy(user_id):
        return "evt_live"

    veto = _CullVeto(check=busy)
    await veto("u")
    await veto("u")
    await veto("u")

    # Two attempts for one (user, run): the failure retried, the success did
    # not repeat. Not three — that would make rows a function of run duration.
    assert attempts == ["evt_live", "evt_live"]


@pytest.mark.asyncio
async def test_a_lost_cull_disabled_row_is_retried_next_pass(monkeypatch):
    """Same for the pass-level row, and it matters more: keyed off the pass
    number alone, a first row lost to a stalled pool leaves the whole first
    hour of an outage with no trace — during exactly the outage it reports."""
    import narranexus.platform.agent_runtime.executor_reaper as mod

    monkeypatch.setattr(mod, "_BLIND_WARN_EVERY", 30)
    attempts = []

    async def flaky_audit(event_type, **kw):
        attempts.append(event_type)
        return len(attempts) > 2        # first two passes fail to land

    monkeypatch.setattr(mod, "_audit", flaky_audit)

    async def blind(user_id):
        return UNKNOWN_RUN

    veto = _CullVeto(check=blind)
    controller = _FakeController(["a"])
    reaper = ExecutorReaper(controller, _noop_stop, is_busy=veto, ttl_seconds=1)

    for _ in range(4):
        controller._idle = ["a"]
        await reaper.reap_once()

    # Passes 1-3 retry (1 and 2 failed), pass 4 is silent — the row landed on
    # pass 3, so the slow tick resumes.
    assert attempts == ["cull_disabled"] * 3


# --------------------------------------------------------------------------
# The broker's stale-image replacement verdict (second consumer)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_replacement_is_blocked_by_a_live_run(monkeypatch):
    """Same container, same rule as the cull: a live run means hands off."""
    import narranexus.platform.agent_runtime.executor_reaper as mod

    async def live(user_id, *, exclude_run_id=None, caller, consequence):
        return "evt_live"

    monkeypatch.setattr(mod, "live_run_elsewhere", live)
    assert await mod.no_live_recorded_run_for("u") is False


@pytest.mark.asyncio
async def test_stale_replacement_is_allowed_when_nothing_is_live(monkeypatch):
    import narranexus.platform.agent_runtime.executor_reaper as mod

    async def idle(user_id, *, exclude_run_id=None, caller, consequence):
        return None

    monkeypatch.setattr(mod, "live_run_elsewhere", idle)
    assert await mod.no_live_recorded_run_for("u") is True


@pytest.mark.asyncio
async def test_stale_replacement_excludes_the_asking_run(monkeypatch):
    """Step 3's own events row is already 'running' when it asks. Counting
    itself would mean "never replace", and a stale executor after a
    wire-protocol change degrades runs silently."""
    import narranexus.platform.agent_runtime.executor_reaper as mod

    seen = {}

    async def spy(user_id, *, exclude_run_id=None, caller, consequence):
        seen["exclude"] = exclude_run_id
        seen["caller"] = caller
        seen["consequence"] = consequence
        return None

    monkeypatch.setattr(mod, "live_run_elsewhere", spy)
    assert await mod.no_live_recorded_run_for("u", active_run_id="evt_me") is True
    assert seen["exclude"] == "evt_me"
    # Labelled distinctly: the two consumers suffer different consequences
    # when liveness is unreadable (culling stops vs images stop rolling).
    assert seen["caller"] == "stale-replace"
    # ...and the consequence half of the "cannot tell" warning is this
    # consumer's, not the reaper's: unknowable liveness stops images rolling,
    # it does not stop culling.
    assert "images will NOT roll" in seen["consequence"]


@pytest.mark.asyncio
async def test_stale_replacement_is_refused_when_liveness_is_unreadable(monkeypatch):
    """Not knowing must never authorise destroying a container (rule #14) —
    here the cost of being wrong is a killed turn, the benefit a faster image
    roll that self-corrects at the next ensure anyway."""
    async def boom():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", boom
    )
    assert await no_live_recorded_run_for("u") is False


def test_the_log_subject_has_no_default():
    """Both log fields are required on purpose: a default is necessarily one
    consumer's outcome, and the next consumer that omits it inherits that
    text silently — which is the bug this parameter was added to fix. Now the
    omission is a TypeError on the first call."""
    import inspect

    params = inspect.signature(live_run_elsewhere).parameters
    assert params["caller"].default is inspect.Parameter.empty
    assert params["consequence"].default is inspect.Parameter.empty


def test_the_reaper_binds_its_own_log_subject():
    """The signature can only protect a NEW consumer: the reaper binds both
    values in a partial, and a TypeError on its path is swallowed by the
    pass-level handlers that keep one user's failure from aborting a cull. So
    the binding itself needs a test — the counterpart to the stale-replace
    spy above. Read off .keywords rather than calling it, which would hit a
    real DB."""
    assert _REAPER_LIVENESS.func is live_run_elsewhere
    assert _REAPER_LIVENESS.keywords == {
        "caller": "reaper",
        "consequence": "executor idle-culling is OFF",
    }


# --------------------------------------------------------------------------
# The broker can refuse: the container sees holders this side cannot
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_broker_refusal_is_not_counted_as_a_cull(audit_rows):
    """The broker answers `status: busy` with a 200 when the CONTAINER reports
    work — an office-watch session leaves no run row, so this side's oracle
    reads the user as idle. Treating that as stopped would drop the idle stamp
    and leave the container unreclaimed for good."""
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    async def refused_stop(user_id):
        return False

    controller = _FakeController(["u"])
    reaper = ExecutorReaper(
        controller, refused_stop, is_busy=None, ttl_seconds=1
    )

    assert await reaper.reap_once() == []          # not counted as reaped
    assert controller.restamped == ["u"]            # stamp handed back
    assert reaper_status()["refused_busy"] == 1
    # NOT a cull_skipped_busy row: that metric's contract is one row = one run
    # saved, named by its real run id, and this refusal has no run id to name.
    assert audit_rows == []


@pytest.mark.asyncio
async def test_a_successful_stop_still_counts(audit_rows):
    """The other half of the pair: a stop that happened is counted and the
    stamp stays consumed."""
    from narranexus.platform.agent_runtime.executor_reaper import reaper_status

    async def real_stop(user_id):
        return True

    reaper = ExecutorReaper(
        _FakeController(["u"]), real_stop, is_busy=None, ttl_seconds=1
    )
    assert await reaper.reap_once() == ["u"]
    assert reaper_status()["refused_busy"] == 0
