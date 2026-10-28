"""
@file_name: job_schema.py
@author: NetMind.AI
@date: 2025-11-25
@description: Job Module Schema - Job data model definition

Job is the Agent's background task capability, used for handling:
- Non-immediate tasks (delayed execution)
- Scheduled tasks (periodic execution)
- Complex tasks (background execution, non-blocking for user interaction)

Job lifecycle:
1. User expresses requirements through conversation
2. Agent calls job_create to create a Job
3. JobTrigger polls in the background, executing when trigger time is reached
4. During execution, Job information is assembled into a prompt and sent to AgentRuntime
5. Execution results are written to Inbox to notify the user
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as _dt_tz
from enum import Enum
from typing import ClassVar, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# =============================================================================
# Enums
# =============================================================================

class JobType(str, Enum):
    """Job type"""
    ONE_OFF = "one_off"        # One-time task: Execute once at a specified time
    SCHEDULED = "scheduled"    # Periodic task: Repeat according to cron/interval
    ONGOING = "ongoing"        # Ongoing task: Repeat until end condition is met (added 2026-01-21)


class JobStatus(str, Enum):
    """Job status"""
    PENDING = "pending"        # Awaiting first trigger (just created)
    ACTIVE = "active"          # Active (scheduled job running normally)
    RUNNING = "running"        # Currently executing
    PAUSED = "paused"          # Paused (reserved)
    PAUSED_NO_QUOTA = "paused_no_quota"  # Auto-paused: run failed because the
    # owner's free-tier quota is exhausted AND no own provider is configured.
    # NOT rescheduled (would re-fire every interval into the same wall — the
    # infinite-loop bug). Auto-resumes when quota is restored or the user
    # configures their own provider (JobTrigger periodic recheck).
    COOLING = "cooling"        # Transient failure: backing off. cooldown_until
    # holds the earliest retry time; the poller re-arms it to ACTIVE when the
    # cooldown elapses. consecutive_failure_count tracks the retry budget; once
    # it reaches the cap the job escalates to FAILED instead of cooling again.
    BLOCKED = "blocked"        # Waiting on unmet job dependencies (prerequisite
    # jobs not yet COMPLETED). Re-armed to ACTIVE when dependencies are satisfied.
    BLOCKED_FAILED = "blocked_failed"  # A prerequisite job FAILED and this job's
    # on_dependency_failure policy is "block". Re-armed if the prerequisite later
    # succeeds, or cleared by the user.
    PAUSED_SPEND_CAP = "paused_spend_cap"  # Auto-paused: the executing user's
    # total LLM spend for the current local day (NARRANEXUS_USER_DAILY_SPEND_CAP_USD,
    # judged in the job's own timezone) was already met/exceeded before this
    # run started (B-14). Auto-resumed by JobTrigger's 15-minute backstop
    # (`_resume_spend_capped_jobs`) once spend is under the cap again — i.e.
    # the next local day, or when ops lower/disable the cap — and manually
    # resumable from the Jobs panel like the other paused states.
    COMPLETED = "completed"    # Completed (one_off finished execution)
    FAILED = "failed"          # Execution failed
    CANCELLED = "cancelled"    # Cancelled (reserved)


# Statuses of a job that is scheduled, in flight, or will run again on its
# own with no owner action: pending / active / running / blocked (waiting on a
# dependency that will complete) / cooling (backoff, re-armed by the clock).
# The duplicate-title check, the similar-title confirmation gate, the
# per-narrative "existing jobs" loads and the "what jobs do I have" summary
# in JobRepository all read this ONE tuple: a status missing here is
# invisible to those surfaces, so a user re-asking for the same thing gets a
# second job (review I5 — BLOCKED had exactly that gap the moment B-16 made
# it reachable). The paused family is deliberately absent (an owner or
# platform decision is pending; a resume re-derives its schedule), as are the
# terminals. This is NOT the due-poll set: get_due_jobs() must stay
# PENDING/ACTIVE only — that restriction is the B-16 fix itself.
LIVE_JOB_STATUSES: tuple[JobStatus, ...] = (
    JobStatus.PENDING,
    JobStatus.ACTIVE,
    JobStatus.RUNNING,
    JobStatus.BLOCKED,
    JobStatus.COOLING,
)


# Statuses an account suspension pauses: the ones the scheduler would pick up
# and START on its own — PENDING / ACTIVE (get_due_jobs) and COOLING (re-armed
# to ACTIVE by the clock). Everything else is left exactly as it is, because
# reinstate restores a suspension-paused job to ACTIVE and only these three
# are correctly restored that way (review I1):
# - BLOCKED / BLOCKED_FAILED wait on a dependency; flattening them to paused
#   and back to ACTIVE would start a downstream job before its upstream output
#   exists. They are never due, so a suspended account's chain cannot run.
# - RUNNING is in flight; `_finalize_job_execution` rewrites the row when the
#   run ends, and JobTrigger's enqueue gate pauses it if it comes due again.
# - PAUSED_NO_QUOTA / PAUSED_SPEND_CAP keep their own reason; if a backstop
#   later flips one to ACTIVE, the same enqueue gate pauses it before it runs.
# - PAUSED (any reason) and the terminals are never rewritten.
# `JobRepository.pause_jobs_for_execution_principal` reads this ONE tuple.
SUSPENDABLE_JOB_STATUSES: tuple[JobStatus, ...] = (
    JobStatus.PENDING,
    JobStatus.ACTIVE,
    JobStatus.COOLING,
)

class JobOrigin:
    """Surfaces a job can be asked for on, and report back to.

    Deliberately a small closed set rather than "any WorkingSource": every
    value here needs delivery code that actually exists, and a source we can
    record but not deliver to is worse than no record — it would route the
    answer at execution time into a branch that silently does nothing.

    ``MESSAGE_BUS`` currently means a TEAM ROOM. A peer DM is not included: a
    job reporting into an agent-to-agent channel has no human reader, and the
    owner-chat fallback is the honest destination for it.
    """

    MESSAGE_BUS = "message_bus"

    #: Origins with a delivery path. Anything else falls back to owner chat.
    DELIVERABLE = (MESSAGE_BUS,)


class JobUpdateFields(BaseModel):
    """The single source of truth for the job_update tool's MUTABLE field set.

    Every surface that accepts a job update derives its body from this class so
    the field list is declared ONCE: the frontend PUT /api/jobs/{job_id} body
    (JobUpdateBody, which adds agent_id) and the agent-scoped seam route body
    (JobUpdateSeamBody, which adds extra="forbid"). All three — plus the shared
    update_job_from_args keyword signature — carry the same names.

    Why it matters: the seam's whole point is that DirectStore (local) and
    HttpStore (cloud, via this route) write byte-identical results. pydantic's
    default extra="ignore" would let a field added to update_job_from_args + the
    MCP tool but forgotten on the route body be SILENTLY dropped on the HttpStore
    path (success=True, one fewer updated_field) while the DirectStore path
    applies it — exactly the divergence the seam exists to prevent. Sharing the
    field list here removes the copy; the seam body's extra="forbid" turns any
    residual drift into a loud 422 instead of a silent no-write.

    Only-passed-fields-change semantics: every field defaults to None; None means
    "leave unchanged" (see update_job_from_args)."""
    title: Optional[str] = None
    description: Optional[str] = None
    payload: Optional[str] = None
    guidance_text: Optional[str] = None
    trigger_config: Optional[dict] = None
    job_type: Optional[str] = None
    next_run_time: Optional[str] = None
    status: Optional[str] = None
    related_entity_id: Optional[str] = None


# =============================================================================
# Trigger Config
# =============================================================================

class TriggerConfig(BaseModel):
    """
    Trigger configuration

    Uses different fields based on job_type:
    - ONE_OFF: Uses run_at to specify execution time
    - SCHEDULED: Uses cron or interval_seconds to specify period
    - ONGOING: Uses interval_seconds + end_condition / max_iterations

    Examples:
        # One-time task: Execute tomorrow morning at 8am (Asia/Shanghai)
        TriggerConfig(run_at=datetime(2025, 1, 16, 8, 0, 0), timezone="Asia/Shanghai")

        # Periodic task: Every day at 8am (America/New_York)
        TriggerConfig(cron="0 8 * * *", timezone="America/New_York")

        # Periodic task: Every hour
        TriggerConfig(interval_seconds=3600, timezone="Asia/Shanghai")

        # Ongoing task: Check every hour until customer completes purchase, max 10 iterations
        TriggerConfig(
            interval_seconds=3600,
            end_condition="Customer completes purchase or explicitly expresses disinterest",
            max_iterations=10,
            timezone="Asia/Shanghai",
        )
    """

    # === ONE_OFF Configuration ===
    run_at: Optional[datetime] = Field(
        default=None,
        description="Execution time for one-time tasks"
    )

    # === SCHEDULED Configuration (choose one) ===
    cron: Optional[str] = Field(
        default=None,
        description="Cron expression, e.g., '0 8 * * *' means every day at 8am"
    )

    # Upper limit 90 days = 7776000 seconds, prevents LLM from generating unreasonably large values
    MAX_INTERVAL_SECONDS: ClassVar[int] = 7_776_000

    # The fields whose presence makes `timezone` mandatory. ONE list, read by
    # both the write-side validator (`timezone_required_for_time_bearing_triggers`)
    # and the read-side `from_stored_dict` (review I11): the B-15 incident was
    # exactly "the validator learned a field, the reader did not", which only
    # blows up on old production rows. A new time-bearing field is added HERE
    # and both paths follow. `job_recovery` derives its reschedule-editable
    # set from this tuple as well.
    TIME_BEARING_FIELDS: ClassVar[tuple[str, ...]] = ("run_at", "cron", "interval_seconds", "end_at")

    interval_seconds: Optional[int] = Field(
        default=None,
        description="Execution interval (seconds), e.g., 3600 means every hour. Max 7776000 (90 days)."
    )

    # === Scheduling horizon (optional; recurring jobs) ===
    end_at: Optional[datetime] = Field(
        default=None,
        description=(
            "Scheduling horizon for recurring (scheduled / ongoing) jobs: "
            "once the NEXT fire time would land past this local time, the "
            "trigger completes the job instead of rescheduling — a "
            "platform-enforced 'this schedule runs until date X', needing no "
            "model cooperation. Ignored for one_off (a single run has no "
            "next fire to bound). Naive local time interpreted in `timezone` "
            "(same convention as run_at). None = no horizon (unchanged "
            "behavior)."
        ),
    )

    # === Timezone (required for all time-bearing triggers) ===
    timezone: Optional[str] = Field(
        default=None,
        description=(
            "IANA timezone name (e.g. 'Asia/Shanghai', 'America/New_York'). "
            "Required for one_off / scheduled / ongoing jobs. "
            "The job's fire time is frozen to this timezone at creation; "
            "later changes to the user's timezone do NOT affect this job."
        ),
        max_length=64,
    )

    @field_validator("interval_seconds")
    @classmethod
    def clamp_interval_seconds(cls, v: Optional[int]) -> Optional[int]:
        """Clamp interval_seconds to a reasonable upper bound."""
        if v is not None and v > cls.MAX_INTERVAL_SECONDS:
            v = cls.MAX_INTERVAL_SECONDS
        return v

    @field_validator("run_at", "end_at")
    @classmethod
    def run_at_must_be_naive(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Reject timezone-aware run_at/end_at; timezone must be specified via
        the timezone field (one timezone convention per model)."""
        if v is not None and v.tzinfo is not None:
            raise ValueError(
                "run_at/end_at must be naive (no tzinfo). Use the `timezone` "
                "field to declare timezone; do not attach offset or tzinfo."
            )
        return v

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_iana(cls, v: Optional[str]) -> Optional[str]:
        """Validate that timezone is a recognised IANA name via zoneinfo."""
        if v is None:
            return v
        if not v.strip():
            raise ValueError("timezone must be a non-empty IANA name")
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, KeyError) as e:
            raise ValueError(
                f"timezone '{v}' is not a valid IANA name "
                f"(e.g. 'Asia/Shanghai', 'America/New_York'): {e}"
            ) from e
        return v

    @model_validator(mode="after")
    def timezone_required_for_time_bearing_triggers(self) -> "TriggerConfig":
        """Require timezone whenever any time-bearing field is set."""
        has_time_field = any(
            getattr(self, f) is not None for f in self.TIME_BEARING_FIELDS
        )
        if has_time_field and self.timezone is None:
            raise ValueError(
                "timezone is required when run_at / cron / interval_seconds "
                "is set. Use IANA name like 'Asia/Shanghai'."
            )
        return self

    @classmethod
    def from_stored_dict(cls, data: Optional[dict]) -> "TriggerConfig":
        """Build a TriggerConfig from a raw DB-stored dict, tolerating rows
        written before `timezone_required_for_time_bearing_triggers` existed
        (2026-04-21) — e.g. `{'cron': '0 13 * * 1-5'}` with no `timezone` key
        at all. `TriggerConfig(**data)` raises `ValidationError` on those, by
        design, for NEW writes (`TestTriggerConfigTimezoneRequired` pins that);
        this is the READ-side counterpart used only when reconstructing an
        already-persisted row, never for validating user/LLM-supplied input.

        Defaults the missing timezone to UTC **in memory only** — the caller
        must not write the defaulted value back to the row; a legacy row
        that never recorded its author's real timezone should stay
        unannotated in storage, not be silently rewritten with a guess.
        An explicit-but-invalid timezone (e.g. 'CST', or an empty string)
        still raises: this only fills in an ABSENT value (key missing or
        None), it never repairs a bad one (review M1).
        """
        data = dict(data or {})
        has_time_field = any(
            data.get(f) is not None for f in cls.TIME_BEARING_FIELDS
        )
        if has_time_field and data.get("timezone") is None:
            data["timezone"] = "UTC"
        return cls(**data)

    @classmethod
    def immediate(cls) -> "TriggerConfig":
        """Canonical "fire now" one_off trigger.

        run_at is the current UTC wall-clock as a NAIVE datetime (tzinfo lives
        in the timezone field, per the run_at_must_be_naive contract); with
        timezone='UTC', compute_next_run resolves it to "now". Use this instead
        of hand-building a trigger dict so no caller ever attaches tzinfo or
        omits timezone — the root cause of the /api/jobs/complex failure where
        a hand-rolled {"trigger_type": "immediate", "run_at": utc_now()} was
        rejected by the naive-run_at validator.
        """
        return cls(
            run_at=datetime.now(_dt_tz.utc).replace(tzinfo=None),
            timezone="UTC",
        )

    # === ONGOING Configuration (added 2026-01-21) ===
    end_condition: Optional[str] = Field(
        default=None,
        description="End condition description (natural language), LLM determines if met"
    )

    max_iterations: Optional[int] = Field(
        default=None,
        description="Maximum execution count, auto-ends when reached (even if end_condition not met)"
    )


# =============================================================================
# Job Model
# =============================================================================

class JobModel(BaseModel):
    """
    Job data model

    Core field descriptions:
    - job_id: Unique business identifier (UUID), used for API and logging
    - agent_id: Owning Agent, which Agent created and executes the Job
    - user_id: Owning user, who receives the Job result notification
    - payload: Natural language execution instruction, assembled into a prompt by JobTrigger and sent to AgentRuntime
    - process: Execution records, storing event_id for each execution

    State transitions:
    - ONE_OFF: PENDING -> RUNNING -> COMPLETED/FAILED
    - SCHEDULED: PENDING -> ACTIVE -> RUNNING -> ACTIVE (loop)
    """

    # === Database ID ===
    id: Optional[int] = Field(
        default=None,
        description="Database auto-increment ID"
    )

    # === Business Identifier ===
    job_id: str = Field(
        ...,
        max_length=64,
        description="Unique Job identifier (UUID)"
    )

    # === Ownership ===
    agent_id: str = Field(
        ...,
        max_length=64,
        description="Owning Agent ID"
    )

    user_id: str = Field(
        ...,
        max_length=64,
        description="Owning User ID (who receives the Job result notification)"
    )

    # === Instance Association (added 2025-12-24) ===
    instance_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Associated JobModule Instance ID"
    )

    # === Basic Information ===
    title: str = Field(
        ...,
        max_length=255,
        description="Job title (brief description)"
    )

    description: str = Field(
        ...,
        description="Job detailed description (preserves user's original words or detailed explanation)"
    )

    # === Trigger Configuration ===
    job_type: JobType = Field(
        ...,
        description="Job type: one_off / scheduled"
    )

    trigger_config: TriggerConfig = Field(
        ...,
        description="Trigger configuration"
    )

    # === Execution Instruction ===
    payload: str = Field(
        ...,
        description="Natural language instruction for execution, assembled and sent to AgentRuntime"
    )

    # === Status ===
    status: JobStatus = Field(
        default=JobStatus.PENDING,
        description="Job current status"
    )

    # === Execution Records ===
    process: List[str] = Field(
        default_factory=list,
        description="Detail records of this execution."
    )

    last_run_time: Optional[datetime] = Field(
        default=None,
        description="Last execution time"
    )

    next_run_time: Optional[datetime] = Field(
        default=None,
        description="Next execution time (calculated by JobTrigger)"
    )

    next_run_at_local: Optional[str] = Field(
        default=None,
        max_length=32,
        description=(
            "Next fire time in user-local naive ISO 8601 "
            "(e.g. '2026-05-01T08:00:00'). Pair with next_run_tz. "
            "LLM- and UI-facing view. Never use next_run_time (UTC) for display."
        ),
    )
    next_run_tz: Optional[str] = Field(
        default=None,
        max_length=64,
        description="IANA timezone associated with next_run_at_local (frozen at job creation)",
    )
    last_run_at_local: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Most recent fire time in user-local naive ISO 8601",
    )
    last_run_tz: Optional[str] = Field(
        default=None,
        max_length=64,
        description="IANA timezone associated with last_run_at_local",
    )

    last_error: Optional[str] = Field(
        default=None,
        description="Error message from the most recent execution"
    )

    started_at: Optional[datetime] = Field(
        default=None,
        description="Current execution start time (for detecting timed-out tasks)"
    )

    # === Notification Configuration ===
    notification_method: str = Field(
        default="inbox",
        description="Notification method: none / inbox / future extensions"
    )

    # === Origin (2026-08-14) — where this job was asked for ===
    origin_source: Optional[str] = Field(
        default=None,
        max_length=32,
        description=(
            "WorkingSource-shaped label for the surface that asked for this "
            "job (see JobOrigin). Empty = the owner's chat, which is both the "
            "historical behaviour and the fallback that always exists."
        ),
    )
    origin_channel_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "The room within origin_source to report back to. Meaningless "
            "without origin_source, which is why the two travel together."
        ),
    )


    # === Related Entity (Feature 2.2.1, modified 2026-01-20) ===
    related_entity_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Target user ID. Job execution uses this ID as the principal identity (loads their context, Narrative, etc.)"
    )

    # === Narrative Association (Feature 3.1) ===
    narrative_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Associated Narrative ID, for loading conversation history and context summary"
    )

    # === ONGOING Related Fields (added 2026-01-21) ===
    monitored_job_ids: Optional[List[str]] = Field(
        default=None,
        description="Monitored Job mode: List of other Job IDs monitored by this Job"
    )

    iteration_count: int = Field(
        default=0,
        description="ONGOING type: Current number of executions"
    )

    # === Resilience / backoff (added 2026-06-01) ===
    consecutive_failure_count: int = Field(
        default=0,
        description="Consecutive transient-failure count. Drives exponential "
        "backoff (COOLING) and escalation to FAILED at the cap. Reset to 0 on "
        "any successful run."
    )

    cooldown_until: Optional[datetime] = Field(
        default=None,
        description="Earliest UTC time a COOLING job may retry. get_due_jobs "
        "ignores jobs still cooling; the poller re-arms them to ACTIVE once "
        "this passes."
    )

    paused_reason: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Why a non-running, non-terminal job is paused: "
        "no_quota / repeated_failure / dependency_failed / user."
    )

    paused_at: Optional[datetime] = Field(
        default=None,
        description="When the job entered its current paused/blocked state."
    )

    # === Metadata ===
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="Creation time"
    )

    updated_at: datetime = Field(
        default_factory=datetime.now,
        description="Update time"
    )

    limit: int = Field(
        default=10,
        description="Return count limit"
    )


# Public alias — downstream code and tests use "Job" as the canonical short name
Job = JobModel


# =============================================================================
# Job Execution Result (Agent Output after execution)
# =============================================================================

class JobExecutionResult(BaseModel):
    """
    Job execution result analysis (generated by LLM)

    Lightweight data model containing only the fields LLM needs to analyze and generate.
    Does not include system management fields like id, created_at, etc.

    Use cases:
    - after_turn calls LLM to analyze execution results
    - LLM returns this structure for updating Job status and scheduling
    """

    job_id: str = Field(
        ...,
        description="Job ID"
    )

    # === Status Determination ===
    status: JobStatus = Field(
        ...,
        description=(
            "Job status after execution. "
            "one_off success -> 'completed'; "
            "scheduled success -> 'active'; "
            "any failure -> 'failed'"
        )
    )

    # === Execution Records ===
    process: List[str] = Field(
        default_factory=list,
        description=(
            "Action records for this execution, 2-5 step descriptions."
        )
    )

    # v2 timezone protocol: next_run_time is NO LONGER LLM-decided.
    # Scheduling is computed deterministically from trigger_config by
    # _job_lifecycle after the LLM returns. The LLM only decides status
    # (active / completed / failed), process, and notification intent.

    # === Error Information ===
    last_error: Optional[str] = Field(
        default=None,
        description="Error description on execution failure; null on success"
    )

    # === Notification Related ===
    should_notify: bool = Field(
        default=True,
        description="Whether to notify the user of this execution result, usually true"
    )

    notification_summary: str = Field(
        default="",
        description="Notification summary, 1-2 concise sentences for Inbox messages"
    )


# =============================================================================
# ONGOING Job Execution Result (added 2026-01-21)
# =============================================================================

class OngoingExecutionResult(BaseModel):
    """
    Execution result analysis for ONGOING type Jobs (generated by LLM)

    Used by after_turn to determine whether an ONGOING Job should continue executing.

    Key fields:
    - is_end_condition_met: LLM determines whether the end condition is met
    - should_continue: Comprehensive judgment on whether to continue execution
    - progress_summary: Current progress summary
    """

    job_id: str = Field(
        ...,
        description="Job ID"
    )

    # === End Condition Determination ===
    is_end_condition_met: bool = Field(
        ...,
        description="Whether the end condition described in trigger_config.end_condition is met"
    )

    end_condition_reason: str = Field(
        default="",
        description="Detailed reasoning for the end condition determination"
    )

    # === Continue Execution Determination ===
    should_continue: bool = Field(
        ...,
        description="Whether to continue execution. When False, Job enters COMPLETED status"
    )

    # === Progress Records ===
    progress_summary: str = Field(
        default="",
        description="Progress summary of this execution, for cumulative recording"
    )

    process: List[str] = Field(
        default_factory=list,
        description="Action records for this execution, 2-5 step descriptions"
    )

    # v2 timezone protocol: next_run_time is NO LONGER LLM-decided here
    # either. Scheduling is derived from trigger_config in _job_lifecycle.

    # === Notification Related ===
    should_notify: bool = Field(
        default=False,
        description="Whether to notify the user of this execution result. ONGOING usually only notifies on completion"
    )

    notification_summary: str = Field(
        default="",
        description="Notification summary (only used when should_notify=True)"
    )
