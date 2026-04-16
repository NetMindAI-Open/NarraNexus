"""
@file_name: _dashboard_schema.py
@author: NarraNexus
@date: 2026-04-13
@description: Pydantic response types for GET /api/dashboard/agents-status.

Discriminated union via `owned_by_viewer` (Literal[True] / Literal[False])
ensures public-variant responses cannot accidentally include owner-only
fields — the type system enforces the permission boundary, not ad-hoc
masking. See design doc TDR-5 + security critic C-2.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


Kind = Literal[
    "idle", "CHAT", "JOB", "MESSAGE_BUS", "A2A", "CALLBACK", "SKILL_STUDY", "LARK"
]

CountBucket = Literal["0", "1-2", "3-5", "6-10", "10+"]


class MessageBusDetails(BaseModel):
    src_channel: str | None = None
    dst_channel: str | None = None


class StatusCommon(BaseModel):
    """Status shape visible to non-owners — no MessageBusDetails."""

    kind: Kind
    last_activity_at: str | None = None
    started_at: str | None = None  # None when kind == 'idle'


class StatusWithDetails(StatusCommon):
    """Owner-visible status — may include MESSAGE_BUS src/dst channel."""

    details: MessageBusDetails | None = None


class JobProgress(BaseModel):
    """v2.1: step-level progress if emitted by the job; else None."""
    current_step: int
    total_steps: int
    stage_name: str | None = None
    estimated_pct: float | None = None  # 0..100


class SessionInfoResp(BaseModel):
    session_id: str
    user_display: str
    channel: str
    started_at: str
    # v2.1: inline preview for the session item row (before user clicks to expand)
    user_last_message_preview: str | None = None


class RunningJob(BaseModel):
    job_id: str
    title: str
    job_type: str
    started_at: str | None = None
    # v2.1 additions
    description: str | None = None
    progress: JobProgress | None = None


class PendingJob(BaseModel):
    job_id: str
    title: str
    job_type: str
    next_run_time: str | None = None
    # v2.1 additions
    description: str | None = None
    # Human-readable status variant: "pending" / "active" / "blocked" / "paused" / "failed"
    # Needed because we now surface all live states (not just pending/active).
    queue_status: Literal['pending', 'active', 'blocked', 'paused', 'failed'] = 'pending'


class QueueCounts(BaseModel):
    """v2.1: how many jobs are in each live state for this agent."""
    running: int = 0
    active: int = 0
    pending: int = 0
    blocked: int = 0
    paused: int = 0
    failed: int = 0
    total: int = 0


class RecentEvent(BaseModel):
    """v2.1: one item in the per-agent 'Recent' feed (last 3 events)."""
    event_id: str
    kind: Literal['completed', 'running', 'failed', 'chat', 'other']
    verb: str          # Human-readable: "Completed daily-digest" / "Failed sync-rag" / "Chat with Alice"
    target: str | None = None
    duration_ms: int | None = None
    created_at: str


class MetricsToday(BaseModel):
    """v2.1: per-agent today stats shown at card footer."""
    runs_ok: int = 0
    errors: int = 0
    avg_duration_ms: int | None = None
    avg_duration_trend: Literal['up', 'down', 'flat', 'unknown'] = 'unknown'
    token_cost_cents: int | None = None  # None when no data source


class AttentionBannerAction(BaseModel):
    label: str
    endpoint: str  # relative path the frontend can POST to
    method: Literal['POST', 'GET'] = 'POST'


class AttentionBanner(BaseModel):
    """v2.1: top-of-card notice needing user attention (error / blocked / paused)."""
    level: Literal['error', 'warning', 'info']
    kind: Literal['job_failed', 'job_blocked', 'jobs_paused', 'slow_response']
    message: str
    action: AttentionBannerAction | None = None


class EnhancedSignals(BaseModel):
    recent_errors_1h: int = 0
    token_rate_1h: int | None = None  # None when source unavailable (frontend shows N/A)
    active_narratives: int = 0
    unread_bus_messages: int = 0


class StaleInstance(BaseModel):
    """v2.2 G3: an in_progress module instance past the stale threshold.

    Surfaced to UI so users can spot zombie state; does NOT raise health=error.
    """

    instance_id: str
    module_class: str
    description: str | None = None


class PublicAgentStatus(BaseModel):
    """Shape returned for public agents where viewer != owner.

    extra='forbid' ensures that even if the factory accidentally tries to
    pass an owner-only field, Pydantic rejects it at construction time.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str
    description: str | None = None
    is_public: Literal[True] = True
    owned_by_viewer: Literal[False] = False
    status: StatusCommon  # no details field
    running_count_bucket: CountBucket


class OwnedAgentStatus(BaseModel):
    """Full shape returned for agents owned by the viewer."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str
    description: str | None = None
    is_public: bool
    owned_by_viewer: Literal[True] = True
    status: StatusWithDetails
    running_count: int
    action_line: str | None = None  # None → frontend renders "—"
    # Human-language verb: "Serving 3 users" / "Running: weekly-report" / "Idle 4m ago".
    # v2.1: richer than kind+action_line; drives the card's primary narrative row.
    verb_line: str | None = None
    sessions: list[SessionInfoResp] = Field(default_factory=list)
    running_jobs: list[RunningJob] = Field(default_factory=list)
    pending_jobs: list[PendingJob] = Field(default_factory=list)
    enhanced: EnhancedSignals
    # v2.1 additions
    queue: QueueCounts = Field(default_factory=QueueCounts)
    recent_events: list[RecentEvent] = Field(default_factory=list)
    metrics_today: MetricsToday = Field(default_factory=MetricsToday)
    attention_banners: list[AttentionBanner] = Field(default_factory=list)
    # Status rail color: derived server-side for consistent client rendering.
    health: Literal['healthy_running', 'healthy_idle', 'idle_long', 'warning', 'error', 'paused'] = 'healthy_idle'
    # v2.2 G3: module instances stuck in_progress past threshold; UI surfaces zombie badge.
    # Does NOT affect health — stale modules are surfaced for visibility, not alerted on.
    stale_instances: list[StaleInstance] = Field(
        default_factory=list,
        description="Module instances stuck in_progress past threshold; UI shows zombie badge but no alert.",
    )


# FastAPI response_model + pydantic v2 discriminated union
AgentStatus = Annotated[
    Union[OwnedAgentStatus, PublicAgentStatus],
    Field(discriminator="owned_by_viewer"),
]


class DashboardResponse(BaseModel):
    success: bool = True
    error: str | None = None
    agents: list[AgentStatus] = Field(default_factory=list)
