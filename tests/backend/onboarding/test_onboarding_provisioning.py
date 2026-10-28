"""
@file_name: test_onboarding_provisioning.py
@author: Bin Liang
@date: 2026-08-19
@description: Unit tests for ensure_guide_agent — the login-time guide-agent
provisioning seam. Pins: the env kill-switch + the backfill brake, user-level
idempotency on a TOP-LEVEL metadata key (survives agent deletion AND the
checklist endpoint's onboarding_progress rewrite), the has-agents skip, the
claim-FIRST ordering (marker before provisioning — the concurrency defense),
the happy-path sequence (single provision call carrying profile+extras →
tag → guide skill → daily SCHEDULED job), best-effort semantics for the job
step, and per-user in-process serialization of concurrent calls.

Collaborators are faked here (parameter-pipe assertions); the persisted-row
contract is covered by test_onboarding_provisioning_integration.py, which
runs the real thing against the db_client fixture.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import backend.onboarding.provisioning as ob
from backend.onboarding.personas import PERSONAS


class Recorder:
    def __init__(self):
        self.calls = []


class FakeUserRepo:
    """In-memory users table: user_id -> metadata dict (None = no user)."""

    instances = None  # set per-test to share state across constructions
    recorder = None

    def __init__(self, db):
        self.state = FakeUserRepo.instances

    async def get_user(self, user_id):
        # Real awaits, not sync-through: the concurrency test below relies on
        # these yield points to model the real DB client's suspension between
        # the marker read and the marker write — without them, two gathered
        # coroutines never interleave and the per-user lock is untestable
        # (deleting the lock would stay green).
        await asyncio.sleep(0)
        if user_id not in self.state:
            return None
        return SimpleNamespace(
            user_id=user_id, metadata=self.state[user_id], timezone="Asia/Shanghai"
        )

    async def update_user(self, user_id, updates):
        await asyncio.sleep(0)
        if FakeUserRepo.recorder is not None:
            FakeUserRepo.recorder.calls.append(("update_user", updates))
        self.state[user_id] = updates.get("metadata", self.state.get(user_id))
        return 1


def _wire(monkeypatch, rec, *, users, agents=(), job_fails=False, provision_delay=0.0):
    FakeUserRepo.instances = users
    FakeUserRepo.recorder = rec

    class FakeAgentRepo:
        def __init__(self, db):
            pass

        async def find_one(self, filters=None):
            rec.calls.append(("find_one", filters))
            return list(agents)[0] if agents else None

        async def get_agent(self, agent_id):
            return SimpleNamespace(agent_id=agent_id, agent_metadata={})

        async def update_agent(self, agent_id, updates):
            rec.calls.append(("update_agent", updates))

    async def fake_provision(db, **kw):
        if provision_delay:
            await asyncio.sleep(provision_delay)
        rec.calls.append(("provision", kw))
        return SimpleNamespace(agent_id=kw["agent_id"], bootstrap_active=True, warnings=[])

    class FakeSkillSvc:
        async def install(self, agent_id, user_id, skill_id, version=None):
            rec.calls.append(("install_skill", skill_id))
            return SimpleNamespace(status="installed")

    class FakeJobSvc:
        def __init__(self, db):
            pass

        async def create_job_with_instance(self, **kw):
            rec.calls.append(("create_job", kw))
            if job_fails:
                return {"success": False, "error": "boom"}
            return {"success": True, "job_id": "job_123"}

    monkeypatch.setattr(
        "narranexus.platform.repository.user_repository.UserRepository", FakeUserRepo
    )
    monkeypatch.setattr("narranexus.platform.repository.AgentRepository", FakeAgentRepo)
    monkeypatch.setattr(
        "narranexus.platform.bootstrap.provision.provision_new_agent", fake_provision
    )
    monkeypatch.setattr(
        "narranexus.platform.marketplace.skill_marketplace_service.SkillMarketplaceService",
        FakeSkillSvc,
    )
    monkeypatch.setattr(
        "narranexus_plugins.job_module.job_service.JobInstanceService", FakeJobSvc
    )
    monkeypatch.setattr(ob, "is_cloud_mode", lambda: True)


def _enable(monkeypatch):
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    # Most tests here exercise the provisioning flow itself with is_new_user
    # combinations; the backfill brake (default OFF) gets its own tests.
    monkeypatch.setenv(ob.BACKFILL_ENV_FLAG, "1")


def test_importing_provisioning_registers_the_profile():
    """The PRODUCTION registration point is provisioning.py's side-effect
    `import backend.onboarding.profile` — get_profile falls back to "default"
    silently on an unknown name, so without this pin an import cleanup could
    strip every guide agent's persona/greeting with all tests green."""
    from narranexus.platform.bootstrap.profiles import get_profile

    # `ob` (backend.onboarding.provisioning) is imported at module top.
    assert get_profile("onboarding").name == "onboarding"


def test_kill_switch_disables_everything(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {}})
    monkeypatch.setenv(ob.ENV_FLAG, "0")
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    assert res == {"skipped": "disabled"}
    assert rec.calls == []  # zero repo/db work when disabled


def test_flag_defaults(monkeypatch):
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    monkeypatch.delenv(ob.BACKFILL_ENV_FLAG, raising=False)
    # Master switch defaults ON; the backfill brake defaults OFF (the
    # existing-zero-agent population is the unbounded cost face — it opts in).
    assert ob.is_guide_agent_enabled() is True
    assert ob.is_backfill_enabled() is False
    monkeypatch.setenv(ob.ENV_FLAG, "false")
    assert ob.is_guide_agent_enabled() is False
    # Incident-keyboard spellings all count as off for the master switch.
    for spelling in ("0", "off", "disabled", ""):
        monkeypatch.setenv(ob.ENV_FLAG, spelling)
        assert ob.is_guide_agent_enabled() is False
    monkeypatch.setenv(ob.BACKFILL_ENV_FLAG, "yes")
    assert ob.is_backfill_enabled() is True


def test_backfill_brake_defaults_to_new_users_only(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {}})
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    monkeypatch.delenv(ob.BACKFILL_ENV_FLAG, raising=False)  # default OFF
    # Returning (non-new) login: braked before any DB work.
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=False))
    assert res == {"skipped": "backfill_disabled"}
    assert rec.calls == []
    # Brand-new signup still provisions.
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    assert res.get("provisioned") is True


def test_backfill_opt_in_covers_returning_users(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {}})
    monkeypatch.delenv(ob.ENV_FLAG, raising=False)
    monkeypatch.setenv(ob.BACKFILL_ENV_FLAG, "1")
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=False))
    assert res.get("provisioned") is True


def test_marker_makes_it_idempotent(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {ob.GUIDE_METADATA_FLAG: True}})
    _enable(monkeypatch)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    assert res == {"skipped": "already_provisioned"}
    assert all(c[0] != "provision" for c in rec.calls)


def test_missing_user_skips(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={})
    _enable(monkeypatch)
    res = asyncio.run(ob.ensure_guide_agent(object(), "ghost", is_new_user=True))
    assert res == {"skipped": "no_user"}


def test_user_with_agents_gets_marker_but_no_guide(monkeypatch):
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users, agents=[SimpleNamespace(agent_id="a1")])
    _enable(monkeypatch)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=False))
    assert res == {"skipped": "has_agents"}
    assert all(c[0] != "provision" for c in rec.calls)
    # Marker written so later logins short-circuit before the agents query.
    assert users["u1"][ob.GUIDE_METADATA_FLAG] is True


def test_happy_path_full_sequence_and_claim_first(monkeypatch):
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users)
    _enable(monkeypatch)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))

    assert res["provisioned"] is True and res["warnings"] == []
    steps = [c[0] for c in rec.calls]
    # CLAIM FIRST: the marker write must land BEFORE provisioning starts —
    # that ordering is the whole concurrency defense.
    assert steps.index("update_user") < steps.index("provision")
    assert "install_skill" in steps and "create_job" in steps

    prov_kw = next(kw for name, kw in rec.calls if name == "provision")
    # Single provision call carries the real profile + its render extras —
    # no none-then-reapply window with a blank persisted greeting.
    assert prov_kw["bootstrap_profile"] == "onboarding"
    extra = prov_kw["bootstrap_ctx_extra"]
    assert {"persona_key", "topic_index", "is_local"} <= set(extra)
    assert extra["is_local"] is False  # is_cloud_mode() wired True
    assert any(p["awareness"] in prov_kw["awareness"] for p in PERSONAS)
    assert prov_kw["agent_name"].count("_") >= 2  # three-group random name

    assert ("install_skill", ob.GUIDE_SKILL_ID) in rec.calls

    job_kw = next(kw for name, kw in rec.calls if name == "create_job")
    # SCHEDULED, not ongoing: ongoing's iteration counter + end_condition
    # analysis also fire on every chat event (cost + early COMPLETED).
    assert job_kw["job_type"] == "scheduled"
    tc = job_kw["trigger_config"]
    assert tc["interval_seconds"] == 86400 and tc["timezone"] == "Asia/Shanghai"
    # The PLATFORM brake: a naive-local end_at the trigger enforces. The
    # payload quotes end_at's date MINUS ONE DAY — not end_at's own day:
    # compute_next_run schedules from actual completion time, so per-run
    # drift makes fire #13 the last one the horizon allows; end_at's own day
    # never fires (round-3 review finding A — do NOT "fix" this back to
    # end_at.date()). "or later" covers drift pushing that last fire past a
    # midnight. The drift-simulation test below is the behavioral guard.
    from datetime import datetime as _dt, timedelta as _td

    end_at = _dt.fromisoformat(tc["end_at"])
    assert end_at.tzinfo is None
    goodbye_day = (end_at - _td(days=1)).date().isoformat()
    assert f"If today's date is {goodbye_day} or later" in job_kw["payload"]
    assert job_kw["title"] == ob.CHECKIN_JOB_TITLE
    # The payload carries the model-judged exits: 3-strikes plus the goodbye
    # script for the platform-enforced end_at horizon (date equality with
    # end_at is asserted above).
    assert "three consecutive" in job_kw["payload"]

    tag = next(kw for name, kw in rec.calls if name == "update_agent")
    assert tag["agent_metadata"]["provisioned_source"] == "onboarding"

    assert users["u1"][ob.GUIDE_METADATA_FLAG] is True


def test_local_mode_flows_into_extras_and_awareness(monkeypatch):
    rec = Recorder()
    _wire(monkeypatch, rec, users={"u1": {}})
    monkeypatch.setattr(ob, "is_cloud_mode", lambda: False)
    _enable(monkeypatch)
    asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    prov_kw = next(kw for name, kw in rec.calls if name == "provision")
    assert prov_kw["bootstrap_ctx_extra"]["is_local"] is True
    assert "LOCAL INSTALL" in prov_kw["awareness"]


def test_job_failure_is_best_effort_and_marker_still_written(monkeypatch):
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users, job_fails=True)
    _enable(monkeypatch)
    res = asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    assert res["provisioned"] is True
    assert any("checkin_job" in w for w in res["warnings"])
    assert res["job_id"] is None
    assert users["u1"][ob.GUIDE_METADATA_FLAG] is True


def test_marker_write_preserves_sibling_metadata(monkeypatch):
    rec = Recorder()
    users = {"u1": {"onboarding_progress": {"first_agent_created": True}}}
    _wire(monkeypatch, rec, users=users)
    _enable(monkeypatch)
    asyncio.run(ob.ensure_guide_agent(object(), "u1", is_new_user=True))
    meta = users["u1"]
    assert meta["onboarding_progress"] == {"first_agent_created": True}
    assert meta[ob.GUIDE_METADATA_FLAG] is True


def test_concurrent_calls_provision_exactly_once(monkeypatch):
    """Two logins racing (two tabs / client retry): the per-user lock
    serializes them and the second sees the first's marker."""
    rec = Recorder()
    users = {"u1": {}}
    _wire(monkeypatch, rec, users=users, provision_delay=0.01)
    _enable(monkeypatch)

    async def _race():
        return await asyncio.gather(
            ob.ensure_guide_agent(object(), "u1", is_new_user=True),
            ob.ensure_guide_agent(object(), "u1", is_new_user=True),
        )

    r1, r2 = asyncio.run(_race())
    outcomes = sorted(("provisioned" in r1 and "skipped" not in r1,
                       "provisioned" in r2 and "skipped" not in r2))
    assert outcomes == [False, True]
    assert [c[0] for c in rec.calls].count("provision") == 1


def test_safe_timezone_falls_back_on_garbage():
    assert ob._safe_timezone("Asia/Shanghai") == "Asia/Shanghai"
    assert ob._safe_timezone("Not/AZone") == "UTC"
    assert ob._safe_timezone(None) == "UTC"
    assert ob._safe_timezone("") == "UTC"


def _simulate_last_fire_and_goodbye_day(drift, provision_utc=None, tz="UTC"):
    """Run the real fire sequence (compute_next_run schedules each fire from
    the previous run's ACTUAL completion time, so it drifts later each round)
    and return (last_un_crossed_fire_LOCAL_date, quoted_goodbye_day).

    Both dates live on the JOB'S timezone axis: `end_local` is derived from
    `end_at` the same way provisioning does (astimezone(tz)), and the fire date
    is the fire instant converted to `tz` — the day the agent actually reads as
    "today". No UTC-date-vs-local-date mixing, so a non-UTC tz can't introduce a
    spurious off-by-one."""
    from datetime import datetime, timedelta, timezone as dt_tz
    from zoneinfo import ZoneInfo

    from narranexus.platform.utils.job_scheduling import (
        compute_next_run,
        past_schedule_horizon,
    )
    from narranexus.platform.schema.job_schema import JobType, TriggerConfig

    if provision_utc is None:
        provision_utc = datetime(2026, 8, 19, 10, 0, 0, tzinfo=dt_tz.utc)
    zi = ZoneInfo(tz)
    # Production semantics (provisioning._create_checkin_job): end_local is end_at in
    # local time, and the payload quotes the day BEFORE it.
    end_local = (
        (provision_utc + timedelta(days=ob.CHECKIN_END_AFTER_DAYS))
        .astimezone(zi)
        .replace(tzinfo=None)
    )
    goodbye_day = (end_local - timedelta(days=1)).date()  # what provisioning quotes
    tc = TriggerConfig(interval_seconds=86400, timezone=tz, end_at=end_local)

    next_fire = compute_next_run(JobType.SCHEDULED, tc, last_run_utc=provision_utc).utc
    last_fire_date = None
    for _ in range(ob.CHECKIN_END_AFTER_DAYS + 2):  # bounded, no while-true
        if past_schedule_horizon(tc, next_fire):
            break
        # The FIRE instant is when the poller runs the job and the agent reads
        # "today" (in the job's tz) — NOT fire + runtime.
        last_fire_date = next_fire.astimezone(zi).date()
        completion = next_fire + drift  # drift = poller delay + agent_loop runtime
        next_fire = compute_next_run(JobType.SCHEDULED, tc, last_run_utc=completion).utc
    else:
        raise AssertionError("fire sequence never crossed the horizon")
    assert last_fire_date is not None
    return last_fire_date, goodbye_day


def test_goodbye_day_is_reachable_under_realistic_drift():
    """Realistic drift (a check-in is one short message — seconds of runtime):
    the quoted goodbye day MUST get a fire, across several provision times AND
    timezones (the reference is local-date, not UTC-date). Equality is normal;
    a later date means drift crossed a midnight — still covered by the payload's
    "or later" wording. Protects anyone who later changes CHECKIN_END_AFTER_DAYS,
    the interval, or the end_date arithmetic."""
    from datetime import datetime, timedelta, timezone as dt_tz

    provisions = [
        datetime(2026, 8, 19, 0, 30, tzinfo=dt_tz.utc),
        datetime(2026, 8, 19, 10, 0, tzinfo=dt_tz.utc),
        datetime(2026, 8, 19, 15, 0, tzinfo=dt_tz.utc),
        datetime(2026, 8, 19, 23, 30, tzinfo=dt_tz.utc),
    ]
    for tz in ("UTC", "Asia/Shanghai", "America/Los_Angeles"):
        for prov in provisions:
            for drift in (timedelta(seconds=90), timedelta(minutes=20)):
                last_fire_date, goodbye_day = _simulate_last_fire_and_goodbye_day(
                    drift, provision_utc=prov, tz=tz
                )
                assert last_fire_date >= goodbye_day, (
                    f"tz={tz} provision={prov} drift={drift}: "
                    f"last fire {last_fire_date} < goodbye {goodbye_day}"
                )


def test_large_drift_slips_the_goodbye_earlier_accepted_degradation():
    """The "minus one day" budget is NOT stable under arbitrary drift (the
    round-4 "any magnitude" claim was wrong). Once per-run drift grows past the
    provision's local time-of-day, the last un-crossed fire slips a calendar day
    earlier than the quoted goodbye day, so the farewell line is skipped. This
    is an ACCEPTED degradation — pathological for a real check-in (this is ~21h
    of drift per round) and the platform brake completes the job on schedule
    regardless (see test_horizon_always_completes_regardless_of_drift). Pinned
    so the boundary is documented, not hidden behind two lucky drift values."""
    from datetime import datetime, timedelta, timezone as dt_tz

    last_fire_date, goodbye_day = _simulate_last_fire_and_goodbye_day(
        timedelta(hours=21, minutes=36),
        provision_utc=datetime(2026, 8, 19, 10, 0, tzinfo=dt_tz.utc),
        tz="UTC",
    )
    # Goodbye skipped — accepted. The brake is unaffected (test below).
    assert last_fire_date < goodbye_day


def test_horizon_always_completes_regardless_of_drift():
    """Whatever the drift, the platform brake fires: the fire sequence always
    crosses the horizon (the helper raises if it never does), i.e. the job is
    guaranteed to complete. The goodbye is best-effort; the brake is not."""
    from datetime import datetime, timedelta, timezone as dt_tz

    for drift in (
        timedelta(seconds=90),
        timedelta(hours=3),
        timedelta(hours=21, minutes=36),
    ):
        # Does not raise ⇒ the horizon was crossed and the job completes.
        _simulate_last_fire_and_goodbye_day(
            drift,
            provision_utc=datetime(2026, 8, 19, 10, 0, tzinfo=dt_tz.utc),
            tz="UTC",
        )
