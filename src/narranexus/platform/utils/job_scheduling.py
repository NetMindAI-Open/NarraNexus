"""
@file_name: job_scheduling.py
@author: Bin Liang
@date: 2026-03-06
@description: Job scheduling utility functions (next-run computation over JobType + TriggerConfig).

Extracted from job_repository.py. Contains the rules for calculating next
execution times based on job type and trigger config.

Placement follows the import graph (rule #21): the repository, the instance
sync service and the job module all need it, and a platform package must not
import a builtin plugin, so it lives with the platform utilities next to the
schema it computes over (``schema/job_schema``) — the job module imports it
from here.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from narranexus.platform.utils import utc_now
from narranexus.platform.schema.job_schema import JobType, TriggerConfig


# =============================================================================
# Atomic scheduling API (v2 timezone protocol, 2026-04-21)
# The legacy timezone-blind `calculate_next_run_time` has been removed.
# =============================================================================

@dataclass(frozen=True)
class NextRunTuple:
    """
    Atomic result of compute_next_run().

    `local` and `utc` are the SAME physical instant expressed in two coordinate
    systems; `tz` is the IANA name of the user-facing coordinate system.
    """
    local: str       # naive ISO 8601, e.g. "2026-05-01T08:00:00"
    tz: str          # IANA, e.g. "Asia/Shanghai"
    utc: datetime    # aware UTC datetime, for poller WHERE clauses


def compute_next_run(
    job_type: JobType,
    trigger_config: TriggerConfig,
    last_run_utc: Optional[datetime] = None,
) -> Optional[NextRunTuple]:
    """
    Single source of truth for "when does this job fire next".

    Returns the fire instant as a (local, tz, utc) triple.

    For one_off: uses trigger_config.run_at (naive) + trigger_config.timezone.
    For scheduled + cron: croniter steps forward from last_run_utc (or utc_now)
        in the job's timezone.
    For scheduled/ongoing + interval: last_run_utc + interval, localized for display.

    Returns None only when the trigger_config carries no fireable time
    (e.g. one_off with run_at=None, or trigger lacks any time field).
    Callers decide terminal-state behavior (one_off post-fire, ongoing end_condition).
    """
    tz_name = trigger_config.timezone
    if tz_name is None:
        # TriggerConfig validator should have caught this; defensive re-check.
        raise ValueError(
            "compute_next_run requires trigger_config.timezone; this should be "
            "enforced by the TriggerConfig validator"
        )
    zi = ZoneInfo(tz_name)

    if job_type == JobType.ONE_OFF:
        if trigger_config.run_at is None:
            return None
        local_dt = trigger_config.run_at  # naive, enforced by validator
        aware_local = local_dt.replace(tzinfo=zi)
        utc_dt = aware_local.astimezone(dt_timezone.utc)
        return NextRunTuple(
            local=local_dt.isoformat(),
            tz=tz_name,
            utc=utc_dt,
        )

    if job_type in (JobType.SCHEDULED, JobType.ONGOING):
        base_utc = last_run_utc if last_run_utc is not None else utc_now()
        if trigger_config.cron:
            from croniter import croniter
            # Use naive local time as croniter base so DST transitions are
            # handled in wall-clock space (not UTC-offset space).  croniter
            # with an aware datetime applies DST-fold logic that produces the
            # wrong hour on transition nights (e.g. "8am" becomes "9am" in EST
            # when the base was already past 8am in EDT).  Passing naive keeps
            # the "next 8am" meaning correct, and zoneinfo.replace() resolves
            # the UTC offset correctly on the output side.
            base_local_naive = base_utc.astimezone(zi).replace(tzinfo=None)
            cron = croniter(trigger_config.cron, base_local_naive)
            next_local_naive = cron.get_next(datetime)  # always naive
            # Attach tz — zoneinfo correctly handles DST fold for the result date
            next_local_aware = next_local_naive.replace(tzinfo=zi)
            next_utc = next_local_aware.astimezone(dt_timezone.utc)
            return NextRunTuple(
                local=next_local_naive.isoformat(),
                tz=tz_name,
                utc=next_utc,
            )
        if trigger_config.interval_seconds:
            next_utc = base_utc + timedelta(seconds=trigger_config.interval_seconds)
            next_local = next_utc.astimezone(zi).replace(tzinfo=None)
            return NextRunTuple(
                local=next_local.isoformat(),
                tz=tz_name,
                utc=next_utc,
            )

    return None


def past_schedule_horizon(
    trigger_config: Optional[TriggerConfig],
    next_fire_utc: datetime,
) -> bool:
    """
    True when a recurring job's next fire would land past its `end_at`
    horizon — the platform-enforced "this schedule runs until date X".

    `end_at` is naive local time in `trigger_config.timezone` (the run_at
    convention); `next_fire_utc` is the aware-UTC instant compute_next_run
    produced. No horizon configured (or no config at all) → False, so every
    pre-existing job behaves exactly as before.
    """
    if trigger_config is None or trigger_config.end_at is None:
        return False
    zi = ZoneInfo(trigger_config.timezone or "UTC")
    end_utc = trigger_config.end_at.replace(tzinfo=zi).astimezone(dt_timezone.utc)
    return next_fire_utc > end_utc
