"""
@file_name: provisioning.py
@author: Bin Liang
@date: 2026-08-19
@description: Idempotent provisioning of the onboarding guide agent — the
"new user's first agent". Called fire-and-forget from the login paths
(netmind-login, local login, local create-user); never blocks or fails a
login. Mirrors ArenaProvisioningService's shape: random name, awareness
persona, targeted skill install, a pre-created routine — but the routine here
is live from day one (the daily check-in IS the product) rather than a paused
consent gate.

Idempotency is USER-level, not agent-level: a write-once TOP-LEVEL
`users.metadata.guide_agent_provisioned` marker. Top-level on purpose — the
`onboarding_progress` sub-dict is wholesale-replaced by
POST /api/auth/onboarding (three fixed checklist booleans), so a marker
nested there would be wiped the first time the user created an agent in the
UI. An agent-level marker alone would resurrect the guide after the user
deletes it — deleting it must stick.

Placement (铁律 #21): lives under backend/ because its only consumers are the
login routes; nothing agent-side imports any of this.
"""

from __future__ import annotations

import asyncio
import os
import random
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger

from narranexus.platform.utils import utc_now
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.deployment_mode import is_cloud_mode

# Load-bearing side-effect import: registers the "onboarding" bootstrap
# profile so provision_new_agent's apply_bootstrap resolves it. Lives HERE
# (not in the package __init__) so the production path has exactly one
# registration point — see the package __init__ docstring; pinned by
# test_importing_provisioning_registers_the_profile.
import backend.onboarding.profile  # noqa: E402,F401  isort:skip

if TYPE_CHECKING:
    from narranexus.platform.repository.user_repository import UserRepository

# Env kill-switch. Default ON: merging to dev exercises it on the dev
# deployment immediately and prod picks it up at the next release, with no
# deploy-repo env change; setting "0"/"false"/"no" disables provisioning.
ENV_FLAG = "NARRANEXUS_ONBOARDING_GUIDE_AGENT"

# Separate brake for the BACKFILL population (existing zero-agent users, who
# get their guide on their next login). Default OFF — the backfill is the
# unbounded cost face (N historical accounts x a daily agent-loop job each,
# on free-tier wallets, including known sock-puppet cohorts), so it opts IN:
# ops sets it to 1 after measuring the zero-agent population. Brand-new
# signups are unaffected by this flag.
BACKFILL_ENV_FLAG = "NARRANEXUS_ONBOARDING_GUIDE_BACKFILL"

# Write-once TOP-LEVEL key in users.metadata (see module docstring for why it
# must not live inside onboarding_progress).
GUIDE_METADATA_FLAG = "guide_agent_provisioned"

# Agent-level tag for ops/statistics + a second idempotency line of defense
# (same key Arena uses with value "arena").
PROVISIONED_SOURCE = "onboarding"

GUIDE_SKILL_ID = "narranexus-guide"

# No emoji in the title on purpose: it is simultaneously the string the
# payload tells the agent to retrieve, the string awareness quotes, and the
# find_active_by_title dedup key — an emoji is the part most likely to drift
# or trip retrieval-side matching, which would silently break the agent's
# self-pause (leaving only the user's manual cancel).
CHECKIN_JOB_TITLE = "Daily check-in"
# The daily check-in is a plain SCHEDULED job, deliberately NOT "ongoing":
# an ONGOING job's iteration counter and end_condition analysis also run on
# every CHAT event (after_turn), which would (a) burn a
# max_iterations budget on ordinary conversation — a chatty first week would
# silently kill the "daily companionship" — and (b) add one Helper-LLM
# analysis call to EVERY chat turn of every new user. A scheduled job fires
# once a day and touches nothing else. Exit paths: (1) the user pausing/
# cancelling in the Jobs panel (the greeting says how); (2) the payload's
# three-ignored-check-ins goodbye + self-pause (model-judged); (3) the
# PLATFORM-ENFORCED trigger_config.end_at horizon — once the next fire would
# land past provision-time + CHECKIN_END_AFTER_DAYS, JobTrigger completes the
# job with no model cooperation. The payload's end-date sentence is the
# polite goodbye script for (3), not the brake itself — and the date it
# quotes is the day BEFORE end_at, worded "{end_date} or later". Why the
# day before: compute_next_run schedules each fire from the previous run's
# ACTUAL completion time, so every round drifts a little later; by fire #13
# the computed next fire (#14) has drifted past end_at = T0+14d, so the
# LAST fire the horizon allows lands on day 13 — end_at's own day never
# fires. Quoting end_at's date meant the goodbye could never run and the
# guide silently vanished mid-smalltalk (review round-3 finding A; the
# drift-simulation test in test_onboarding_provisioning.py pins this).
# "or later" keeps the goodbye firing even if drift pushes the last fire
# past a midnight into day 14.
#
# ROBUSTNESS BOUND: "minus one day" keeps the goodbye reachable as long as
# per-run drift stays small relative to provision's local time-of-day. It is
# NOT stable under arbitrary drift (an earlier draft claimed so — it isn't):
# the fire instant and the horizon crossing do NOT cancel, they leave the last
# allowed fire somewhere in a window of width ~drift, so once per-run drift
# grows past the provision's local time-of-day the last fire can slip one
# calendar day earlier than the quoted day and the goodbye is skipped. That
# only costs the farewell line, never the brake — the platform still completes
# on schedule regardless. For a real check-in (one short message, seconds of
# runtime) drift is negligible and the goodbye always lands; the degraded
# large-drift case is pinned as an ACCEPTED degradation in
# test_onboarding_provisioning.py (not cherry-picked away behind two lucky
# drift values).
CHECKIN_END_AFTER_DAYS = 14
_CHECKIN_JOB_DESCRIPTION = (
    "Once a day, drop by with a fresh topic. Pause or cancel this job "
    "anytime if you'd rather not be pinged."
)
_CHECKIN_JOB_PAYLOAD = (
    "Daily check-in time. FIRST read the recent chat history with your "
    "creator. RULES: (1) If today's date is {end_date} or later, OR your creator "
    "has NEVER replied to your last three consecutive proactive check-ins: "
    "send one graceful goodbye message saying you'll stop reaching out (they "
    "can still message you anytime; skip the goodbye if you already said it), "
    "then use your job retrieval tool to find THIS job (title "
    "'" + CHECKIN_JOB_TITLE + "') and pause it via job_update with your own "
    "agent_id, that job_id, and status='paused' — then do nothing else. "
    "(2) Otherwise send exactly ONE short, lively message in your creator's "
    "language: react to whatever they said last, or open a fresh interesting "
    "topic, and occasionally remind them you can teach them NarraNexus "
    "tricks (read skills/narranexus-guide/SKILL.md first if they asked about "
    "the product). Never send more than one message."
)

# Every spelling someone at an incident keyboard plausibly types to kill it
# (conftest itself uses "off" for NEXUS_DIAG_SHIP; an empty value should read
# as "explicitly cleared", not "on").
_FALSY = ("0", "false", "no", "off", "disabled", "")
_TRUTHY = ("1", "true", "yes", "on")

# Best-effort per-user serialization of concurrent logins (two tabs from the
# same ?token= link, client retries, local create-user immediately followed
# by login). In-process only — the cloud backend runs as a single process, so
# this closes the realistic race; a multi-process deployment would reopen a
# small cross-process window. Entries are popped unconditionally after
# release, which can discard a lock object that queued coroutines still hold
# — a later arrival then gets a fresh lock and skips their queue. That is
# accepted: the claim-first marker write is the actual double-provision
# backstop; the lock only narrows the window.
_inflight_locks: Dict[str, asyncio.Lock] = {}


def is_guide_agent_enabled() -> bool:
    # Default ON; any recognised "off" spelling disables.
    return os.environ.get(ENV_FLAG, "1").strip().lower() not in _FALSY


def is_backfill_enabled() -> bool:
    # Default OFF; requires an explicit truthy opt-in (see BACKFILL_ENV_FLAG).
    return os.environ.get(BACKFILL_ENV_FLAG, "0").strip().lower() in _TRUTHY


async def ensure_guide_agent(
    db: AsyncDatabaseClient, user_id: str, *, is_new_user: bool = False
) -> Dict[str, Any]:
    """
    Ensure this user has been offered their onboarding guide agent exactly
    once. Safe to call on every login: cheap skips for the disabled /
    already-provisioned / already-has-agents cases.

    Returns a small status dict for logging/tests; never raises for the
    best-effort steps (skill install, job creation) — the agent row existing
    is what counts as provisioned.
    """
    if not is_guide_agent_enabled():
        return {"skipped": "disabled"}
    if not is_new_user and not is_backfill_enabled():
        return {"skipped": "backfill_disabled"}

    lock = _inflight_locks.setdefault(user_id, asyncio.Lock())
    try:
        async with lock:
            return await _ensure_locked(db, user_id)
    finally:
        # Unconditional: `async with` released the lock before this runs, so
        # a locked() check would never fire (and entries must not accumulate
        # across event loops — a stale lock bound to a finished loop would
        # blow up the next login's await). See the _inflight_locks comment
        # for why discarding a queued waiter's lock is acceptable.
        _inflight_locks.pop(user_id, None)


async def _ensure_locked(db: AsyncDatabaseClient, user_id: str) -> Dict[str, Any]:
    from narranexus.platform.repository import AgentRepository
    from narranexus.platform.repository.user_repository import UserRepository

    user_repo = UserRepository(db)
    user = await user_repo.get_user(user_id)
    if user is None:
        return {"skipped": "no_user"}
    metadata = dict(user.metadata or {})
    if metadata.get(GUIDE_METADATA_FLAG):
        return {"skipped": "already_provisioned"}

    agent_repo = AgentRepository(db)
    existing = await agent_repo.find_one(filters={"created_by": user_id})
    if existing is not None:
        # The user already lives here (owns agents) — a proactive stranger
        # would be noise. Mark so we never re-evaluate on later logins.
        await _write_marker(user_repo, user_id, metadata)
        return {"skipped": "has_agents"}

    # CLAIM FIRST: write the marker before provisioning, so a concurrent
    # login (or one racing past the in-process lock in a multi-process
    # deployment) short-circuits on the marker instead of creating a second
    # guide + a second daily job. The flip side — a crash between here and
    # add_agent leaves the user guide-less with no retry — is the same
    # write-once posture as the post-step failures below: no path re-runs
    # provisioning, because a retry that half-succeeded once would
    # double-greet.
    await _write_marker(user_repo, user_id, metadata)

    rng = random.Random()
    from backend.naming import generate_name
    from backend.onboarding.personas import (
        pick_persona,
        pick_topic_index,
        render_awareness,
    )

    agent_name = generate_name(rng)
    persona = pick_persona(rng)
    topic_index = pick_topic_index(rng)
    is_local = not is_cloud_mode()

    from narranexus.platform.bootstrap.provision import provision_new_agent

    agent_id = f"agent_{uuid4().hex[:12]}"
    result = await provision_new_agent(
        db,
        agent_id=agent_id,
        user_id=user_id,
        agent_name=agent_name,
        agent_description="Your first NarraNexus companion — ask me anything",
        awareness=render_awareness(agent_name, persona, is_local=is_local),
        bootstrap_profile="onboarding",
        bootstrap_ctx_extra={
            "persona_key": persona["key"],
            "topic_index": topic_index,
            "is_local": is_local,
        },
    )
    warnings = list(result.warnings)
    for w in warnings:
        if w.startswith(("bootstrap", "awareness")):
            # A failed bootstrap delivers a mute guide (empty greeting) whose
            # awareness claims a greeting was already shown; a failed
            # awareness seed delivers a persona-less generic assistant.
            # Error, not warning, because nothing ever retries either.
            logger.error(f"[onboarding] first-run step failed for {agent_id}: {w}")

    # Tag the agent for ops/statistics (and as a secondary idempotency key).
    try:
        agent = await agent_repo.get_agent(agent_id)
        meta = dict((agent.agent_metadata if agent else None) or {})
        meta["provisioned_source"] = PROVISIONED_SOURCE
        await agent_repo.update_agent(agent_id, {"agent_metadata": meta})
    except Exception as e:  # noqa: BLE001 — tag is best-effort
        logger.warning(f"[onboarding] tagging {agent_id} failed: {e}")
        warnings.append(f"metadata_tag: {e}")

    # The guide skill — targeted install (default:false keeps it off ordinary
    # new agents).
    try:
        from narranexus.platform.marketplace.skill_marketplace_service import (
            SkillMarketplaceService,
        )

        await SkillMarketplaceService().install(agent_id, user_id, GUIDE_SKILL_ID)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[onboarding] guide-skill install for {agent_id} failed: {e}")
        warnings.append(f"guide_skill: {e}")

    # The daily check-in routine — created live (rows start PENDING; the job
    # poller fires status IN (pending, active)), first fire ≈ +24h
    # (compute_next_run: interval jobs fire at base + interval), so it never
    # races the greeting. The user can pause/cancel it in the Jobs panel
    # anytime; the greeting says so.
    job_id = None
    try:
        job_id = await _create_checkin_job(db, agent_id, user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[onboarding] check-in job for {agent_id} failed: {e}")
        warnings.append(f"checkin_job: {e}")

    logger.info(
        f"[onboarding] guide agent {agent_id} ({agent_name}, "
        f"persona={persona['key']}) provisioned for {user_id}"
        f"{' with warnings: ' + '; '.join(warnings) if warnings else ''}"
    )
    return {
        "provisioned": True,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "persona": persona["key"],
        "job_id": job_id,
        "warnings": warnings,
    }


async def _write_marker(
    user_repo: "UserRepository", user_id: str, base_metadata: Dict[str, Any]
) -> None:
    """Write-once top-level marker; merge-and-write over the caller's
    already-loaded metadata so sibling keys (onboarding_progress etc.) are
    preserved without a second read (keeps the claim window one round-trip)."""
    if base_metadata.get(GUIDE_METADATA_FLAG):
        return
    metadata = dict(base_metadata)
    metadata[GUIDE_METADATA_FLAG] = True
    await user_repo.update_user(user_id, {"metadata": metadata})


def _safe_timezone(raw: Optional[str]) -> str:
    """users.timezone is user-supplied history; a non-IANA value would make
    TriggerConfig validation reject the whole check-in job."""
    if not raw:
        return "UTC"
    try:
        ZoneInfo(raw)
        return raw
    except Exception:  # noqa: BLE001 — any unresolvable tz falls back
        return "UTC"


async def _create_checkin_job(
    db: AsyncDatabaseClient, agent_id: str, user_id: str
) -> str:
    from narranexus.platform.repository.user_repository import UserRepository
    from narranexus.platform.utils.plugin_services import job_instances

    user = await UserRepository(db).get_user(user_id)
    tz = _safe_timezone(getattr(user, "timezone", None) if user else None)
    # end_local is the naive-local end_at the trigger enforces. The payload
    # quotes the day BEFORE it: per-run drift (compute_next_run schedules
    # from actual completion time) means the last fire the horizon allows
    # lands on day CHECKIN_END_AFTER_DAYS - 1 — quoting end_at's own day
    # would put the goodbye on a day that never fires (see the constant's
    # comment; pinned by the drift-simulation test).
    end_utc = utc_now() + timedelta(days=CHECKIN_END_AFTER_DAYS)
    end_local = end_utc.astimezone(ZoneInfo(tz)).replace(tzinfo=None)
    end_date = (end_local - timedelta(days=1)).date().isoformat()

    result = await job_instances(db).create_job_with_instance(
        agent_id=agent_id,
        user_id=user_id,
        title=CHECKIN_JOB_TITLE,
        description=_CHECKIN_JOB_DESCRIPTION,
        job_type="scheduled",
        trigger_config={
            "interval_seconds": 86400,
            "timezone": tz,
            # Platform-enforced horizon: JobTrigger completes the job once the
            # next fire would land past this local time (see job_trigger.py).
            "end_at": end_local.isoformat(),
        },
        payload=_CHECKIN_JOB_PAYLOAD.format(end_date=end_date),
        # Fixed platform spec, not an LLM guess — skip similarity confirmation.
        confirm_new=True,
    )
    if not result.get("success"):
        raise RuntimeError(f"job creation refused: {result}")
    return result["job_id"]
