"""
@file_name: api_schema.py
@author: NetMind.AI
@date: 2025-12-02
@description: API Request/Response Schema

Centralized management of all API route request and response models

Includes:
- Auth related: LoginRequest, LoginResponse, AgentInfo, etc.
- Agents related: AwarenessResponse, SocialNetworkEntityInfo, etc.
- Jobs related: JobResponse, JobListResponse, etc.
- MCP related: MCPInfo, MCPCreateRequest, etc.
- Files related: FileInfo, FileListResponse, etc.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
# Deep import, not the `narranexus.platform.schema` facade: the facade re-exports
# THIS module's models, so going through it would close an import cycle.
from narranexus.platform.schema.entity_schema import (
    AGENT_TEXT_MAX_LENGTH,
    StrippedText as _StrippedText,
)


# ===== Auth Schemas =====

class LoginRequest(BaseModel):
    """Request model for local-mode login (user_id only — OS user is the
    trust boundary). Cloud login is NetmindLoginRequest."""
    user_id: str


class LoginResponse(BaseModel):
    """Response model for local-mode login"""
    success: bool
    user_id: Optional[str] = None
    error: Optional[str] = None


class NetmindLoginRequest(BaseModel):
    """Request model for NetMind-account login (cloud mode).

    `netmind_token` is the loginToken (JWT) the frontend obtained from
    NetMind's auth API (embedded login form / OAuth popup / ?token= URL
    pass-through). `source` tags the entry channel (e.g. "arena") for
    downstream provisioning; optional and free-form.
    """
    netmind_token: str
    source: Optional[str] = None


class NetmindLoginResponse(BaseModel):
    """Response model for NetMind-account login.

    On success the backend has verified the NetMind token, upserted the
    local user (user_id = NetMind userSystemCode) and issued NarraNexus's
    own JWT — subsequent requests never touch NetMind again.
    """
    success: bool
    user_id: Optional[str] = None
    token: Optional[str] = None
    role: Optional[str] = None
    is_new_user: bool = False
    # Whether this deployment auto-provisions the onboarding guide agent.
    # The frontend gates its "your first agent is already here" coachmark on
    # this so pulling the server-side kill-switch also silences the UI —
    # without it, a disabled deployment would still promise an agent that
    # never appears.
    guide_agent_provisioning: bool = False
    display_name: Optional[str] = None
    email: Optional[str] = None
    error: Optional[str] = None


class ActiveRunInfo(BaseModel):
    """Phase C — summary of the agent's currently running run, if any.

    The frontend uses this to render the "Running" badge on the agent
    card (pulse + glow, sharing the visual language of the Jobs
    status badges). When no run is active, the parent AgentInfo
    carries ``active_run = None``.
    """
    run_id: str
    state: str  # running / cancelling / completed / cancelled / failed
    started_at: Optional[str] = None
    last_event_at: Optional[str] = None
    tool_call_count: int = 0
    current_stage: Optional[str] = None


class BoundChannel(BaseModel):
    """One channel an owned agent is bound to, with whether it is switched on.

    ``active`` is False when the credential row exists but its enable switch
    is off (``is_active`` / ``enabled`` = 0) — configured, not currently
    reachable. Tables without a switch always report True.
    """
    channel: str
    active: bool = True


class AgentInfo(BaseModel):
    """Response model for agent info"""
    agent_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    is_public: bool = False
    created_by: Optional[str] = None
    # Effective agent-slot runtime identity, resolved by GET /api/auth/agents
    # through AgentSlotService.owner_agents_overview — the same overlay the
    # driver dispatch and the system prompt use. Populated for the caller's OWN
    # agents only; a public agent owned by someone else reports None for both
    # (their configuration is not the viewer's business).
    agent_framework: Optional[str] = None
    model: Optional[str] = None
    # Channels bound to this owned agent, each with its on/off state. Public
    # agents owned by another user always expose an empty list.
    bound_channels: List[BoundChannel] = Field(default_factory=list)
    bootstrap_active: bool = False
    # Per-agent first-run greeting (from agent_metadata.bootstrap_greeting,
    # set by scenario provisioners like Arena onboarding). None → the frontend
    # uses the generic default. Single source of truth so the instant
    # frontend greeting and the DB-persisted greeting match (no dup bubble).
    bootstrap_greeting: Optional[str] = None
    # Phase C (2026-05-13): summarise the agent's active run for the
    # frontend "running" badge. None means the agent is not currently
    # running for this user. The query is one event-table SELECT per
    # agent in the list — bounded by agent count which is small.
    active_run: Optional[ActiveRunInfo] = None
    # NM messenger sidebar preview — most recent persisted assistant
    # reply for this agent, truncated server-side to a render-friendly
    # length. The frontend prefers this over deriving from local session
    # state so unselected sidebar rows can show "what did this agent
    # last say" without first loading the conversation. ``None`` means
    # this agent has never produced a reply (fresh, no completed runs).
    # The companion ``last_assistant_at`` lets the row sort/anchor by
    # the same message the preview came from.
    last_assistant_preview: Optional[str] = None
    last_assistant_at: Optional[str] = None


class AgentListResponse(BaseModel):
    """Response model for agent list"""
    success: bool
    agents: List[AgentInfo] = []
    count: int = 0
    error: Optional[str] = None


class CreateAgentRequest(BaseModel):
    """Request model for creating agent. Identity (created_by) comes from
    auth_middleware, never from the body."""
    # Length-capped at the write edge so an over-long name/description is
    # rejected as 422 here, never reaching the DB — the same ceiling the
    # Agent entity model enforces on read (see AGENT_TEXT_MAX_LENGTH).
    # Stripped first, so the cap measures the string that will be stored —
    # see UpdateAgentRequest for why that distinction is load-bearing.
    agent_name: Optional[_StrippedText] = Field(None, max_length=AGENT_TEXT_MAX_LENGTH)
    agent_description: Optional[_StrippedText] = Field(
        None, max_length=AGENT_TEXT_MAX_LENGTH
    )
    # Bootstrap profile name (first-run flow). None/omitted → "default" (today's
    # behavior). Scenario creators (e.g. Arena) use their own profile instead.
    bootstrap: Optional[str] = None
    # Team to attach the new agent to (#43). Set when "Add agent" is clicked
    # under a team in the sidebar. Ownership-checked server-side; an absent /
    # foreign team leaves the agent ungrouped rather than failing creation.
    team_id: Optional[str] = None


class CreateAgentResponse(BaseModel):
    """Response model for creating agent"""
    success: bool
    agent: Optional[AgentInfo] = None
    error: Optional[str] = None


class UpdateAgentRequest(BaseModel):
    """Request model for updating agent"""
    # See CreateAgentRequest — same write-edge length cap (422 on overflow).
    #
    # The cap is measured AFTER stripping (`_StrippedText`), because the value
    # that gets stored is the stripped one: `AgentRepository` normalizes on the
    # way in. Measuring the raw string made ``"x"*255 + " "`` a 422 here while
    # the agent-facing `update_agent_profile` — which measures after stripping
    # — accepted it, one more "same input, two answers" split between the two
    # writers of this row. `None` still means "field not supplied" and is
    # passed through untouched; only that distinguishes it from `""`, which
    # means "clear this field".
    agent_name: Optional[_StrippedText] = Field(None, max_length=AGENT_TEXT_MAX_LENGTH)
    agent_description: Optional[_StrippedText] = Field(
        None, max_length=AGENT_TEXT_MAX_LENGTH
    )
    is_public: Optional[bool] = None


class UpdateAgentResponse(BaseModel):
    """Response model for updating agent"""
    success: bool
    agent: Optional[AgentInfo] = None
    error: Optional[str] = None
    #: agent_id of another agent of the same owner that already answers to the
    #: name just applied. NOT an error — handing a name from one agent to
    #: another is a thing owners do deliberately, and refusing it would be
    #: wrong. Doing it SILENTLY is what started the incident this whole area
    #: exists for (two agents answering to one name, P1 section 02 ①), so the
    #: rename is applied and the collision is reported. Additive field: older
    #: clients ignore it.
    name_clash_with: Optional[str] = None
    #: False when this call renamed the agent but its Awareness identity record
    #: could not be updated (no Awareness instance, or the write failed). The
    #: rename itself DID land — reporting failure for a stored name would be the
    #: worse lie — but "the column moved and the memory did not" is the exact
    #: state the Shenzhen incident was, and it must not be visible only in a
    #: container log that `docker restart` wipes (incident lesson #5). None when
    #: this call had nothing to do about the record — it renamed nothing and
    #: found no stale one. The repair path reports here too: renaming an agent
    #: to the name it already holds is how an already-diverged agent gets fixed,
    #: and "fixed" and "still broken" must not both read as success.
    identity_record_updated: Optional[bool] = None


class DeleteAgentResponse(BaseModel):
    """Response model for deleting agent (cascade)"""
    success: bool
    agent_id: Optional[str] = None
    deleted_counts: Dict[str, int] = {}
    error: Optional[str] = None


class CreateUserRequest(BaseModel):
    """Request model for creating a local user"""
    user_id: str
    display_name: Optional[str] = None


class CreateUserResponse(BaseModel):
    """Response model for creating user"""
    success: bool
    user_id: Optional[str] = None
    # See NetmindLoginResponse.guide_agent_provisioning — same gate for the
    # local-mode signup path.
    guide_agent_provisioning: bool = False
    error: Optional[str] = None


class UpdateTimezoneRequest(BaseModel):
    """Request model for updating the authenticated user's timezone."""
    timezone: str  # IANA timezone format, e.g., 'Asia/Shanghai'


class UpdateTimezoneResponse(BaseModel):
    """Response model for updating user timezone"""
    success: bool
    user_id: Optional[str] = None
    timezone: Optional[str] = None
    error: Optional[str] = None


# ===== Onboarding Schemas =====

class OnboardingProgress(BaseModel):
    """New-user onboarding checklist state, persisted inside users.metadata.

    Flags are write-once-true: a completed step stays completed even if the
    underlying entity is later removed (user creates their first agent then
    deletes it — onboarding still counts it done). This keeps the checklist
    card from oscillating. `dismissed` permanently hides the card.

    `provider_configured` is intentionally NOT stored here — it is derived
    live from the user's provider count by the frontend, because the welcome
    flow decides that step from a live probe, not from stored state.

    `landing_completed` marks the one-time first-run flow (WelcomePage) as
    seen — reached the end OR skipped, both count. Server-side rather than
    localStorage so a user who logs in from another browser/machine is not
    walked through it a second time; existing users are backfilled silently
    (they have agents/providers already, so there is nothing to onboard).
    """
    first_agent_created: bool = False
    template_applied: bool = False
    dismissed: bool = False
    landing_completed: bool = False


class OnboardingResponse(BaseModel):
    """Response model for the onboarding GET / POST endpoints."""
    success: bool
    progress: Optional[OnboardingProgress] = None
    error: Optional[str] = None


class UpdateOnboardingRequest(BaseModel):
    """Mark one or more onboarding steps complete.

    Only fields explicitly set to True are applied (write-once-true) — None
    and False are ignored, so a client can never un-complete a step.
    Identity comes from auth_middleware, never from the body.
    """
    first_agent_created: Optional[bool] = None
    template_applied: Optional[bool] = None
    dismissed: Optional[bool] = None
    landing_completed: Optional[bool] = None


# ===== Awareness Schemas =====

class AwarenessResponse(BaseModel):
    """Response model for awareness endpoint"""
    success: bool
    awareness: Optional[str] = None
    create_time: Optional[str] = None
    update_time: Optional[str] = None
    error: Optional[str] = None


class AwarenessUpdateRequest(BaseModel):
    """Request model for updating awareness"""
    awareness: str


# ===== Social Network Schemas =====

class SocialNetworkEntityInfo(BaseModel):
    """Social network entity info"""
    entity_id: str
    entity_name: Optional[str] = None
    aliases: List[str] = []                    # Cross-system IDs and alternate names
    entity_description: Optional[str] = None
    entity_type: str
    familiarity: str = "known_of"              # direct | known_of
    identity_info: Dict[str, Any] = {}
    contact_info: Dict[str, Any] = {}
    tags: List[str] = []                       # Kept for backward compat
    keywords: List[str] = []                   # Same data as tags, new name
    relationship_strength: float = 0.0
    interaction_count: int = 0
    last_interaction_time: Optional[str] = None
    # New fields (Feature 2.2, 2.3)
    persona: Optional[str] = None              # Communication style/characteristics
    related_job_ids: List[str] = []            # Associated Job IDs
    expertise_domains: List[str] = []          # Expertise domains
    similarity_score: Optional[float] = None   # Similarity score in semantic search


class SocialNetworkResponse(BaseModel):
    """Response model for social network endpoint (single entity)"""
    success: bool
    entity: Optional[SocialNetworkEntityInfo] = None
    error: Optional[str] = None


class SocialNetworkListResponse(BaseModel):
    """Response model for social network list endpoint (all entities)"""
    success: bool
    entities: List[SocialNetworkEntityInfo] = []
    count: int = 0
    error: Optional[str] = None


class SocialNetworkSearchResponse(BaseModel):
    """Response model for social network search endpoint"""
    success: bool
    entities: List[SocialNetworkEntityInfo] = []
    count: int = 0
    search_type: str = "keyword"  # "keyword" or "semantic"
    error: Optional[str] = None


# ===== Chat History Schemas =====

class EventInfo(BaseModel):
    """Event info for chat history"""
    event_id: str
    narrative_id: Optional[str] = None
    narrative_name: Optional[str] = None
    trigger: str
    trigger_source: str
    user_id: Optional[str] = None
    final_output: str
    created_at: str
    event_log: List[Dict[str, Any]] = []


class InstanceInfo(BaseModel):
    """Instance info for displaying in Narrative"""
    instance_id: str
    module_class: str
    description: str = ""
    status: str = "active"
    dependencies: List[str] = []
    config: Dict[str, Any] = {}
    created_at: Optional[str] = None
    user_id: Optional[str] = None  # Used by frontend to filter events by user_id


class NarrativeInfo(BaseModel):
    """Narrative info for chat history"""
    narrative_id: str
    name: str
    description: str
    current_summary: str
    actors: List[Dict[str, str]] = []
    created_at: str
    updated_at: str
    instances: List[InstanceInfo] = []  # Associated Module Instances


class ChatHistoryResponse(BaseModel):
    """Response model for chat history endpoint"""
    success: bool
    narratives: List[NarrativeInfo] = []
    events: List[EventInfo] = []
    narrative_count: int = 0
    event_count: int = 0
    error: Optional[str] = None


class ClearHistoryResponse(BaseModel):
    """Response model for the scoped clear conversation & memory endpoint.

    Reports which scopes ran and per-target counts. `success` is True once the
    DB transaction commits even if `disk_errors` is non-empty (the on-disk
    markdown/trajectory/session deletes are best-effort and idempotent)."""
    success: bool
    scopes: list = []                    # ["conversations", "memory"]
    narrative_ids_deleted: list = []
    narratives_count: int = 0
    events_count: int = 0
    event_stream_count: int = 0
    chat_memory_count: int = 0
    chat_instances_count: int = 0
    agent_messages_count: int = 0
    bus_messages_count: int = 0
    # Every WipeResult counter has to appear here AND in the route's kwargs AND in
    # the dataclass — three copies of one field list, which has already drifted
    # twice (`bus_failures_count` on dev, the two inbox counters when the inbox
    # moved to its own tables). `test_wipe_result_fields_reach_the_api` fails on
    # the next omission rather than leaving it to be noticed in a ticket.
    bus_failures_count: int = 0
    inbox_threads_count: int = 0
    inbox_thread_messages_count: int = 0
    # Found by the coverage test above, not by anyone noticing: these two have
    # been deleted-but-unreported since the wipe grew them.
    report_memory_count: int = 0
    instance_links_count: int = 0
    memory_rows_count: int = 0
    artifacts_count: int = 0
    disk_markdown_removed: bool = False
    disk_trajectories_removed: bool = False
    session_removed: bool = False
    disk_errors: list = []
    error: Optional[str] = None


# ===== Simple Chat History Schemas =====

class SimpleChatMessage(BaseModel):
    """Simplified chat message"""
    role: str  # "user" | "assistant"
    content: str
    timestamp: Optional[str] = None
    narrative_id: Optional[str] = None  # Source Narrative
    working_source: Optional[str] = None  # "chat" | "job" | "lark" | etc.
    message_type: Optional[str] = None  # "chat" (default) | "activity"
    event_id: Optional[str] = None  # Associated Event ID (for loading event_log on demand)
    # User-uploaded attachments referenced by this message (kept as plain
    # dicts to match the JSON shape stored in instance_json_format_memory_chat
    # — the frontend types this as Attachment[]).
    attachments: Optional[List[dict]] = None


class SimpleChatHistoryResponse(BaseModel):
    """
    Simplified chat history response

    Used by the frontend to display recent interaction history with the Agent,
    without distinguishing by Narrative.
    """
    success: bool
    messages: List[SimpleChatMessage] = []
    total_count: int = 0
    error: Optional[str] = None


class EventLogToolCall(BaseModel):
    """A single tool call extracted from event_log"""
    tool_name: str
    tool_input: Dict[str, Any] = {}
    tool_output: Optional[str] = None


class EventLogTimelineEntry(BaseModel):
    """A single entry in the original event_log timeline.

    Preserves the chronological order of thinking / tool_call / tool_output /
    native_output events so the frontend can render history with the same
    inline "think → tool → think → tool → reply" cadence as the live
    streaming TurnTimeline, instead of the legacy "all thinking on top,
    all tools below" grouping that lost time ordering.
    """
    # Discriminator: "thinking" | "tool_call" | "tool_output" | "native_output" | "reply"
    type: str
    # Plain-text content (thinking / native_output / reply); empty for tool entries.
    content: Optional[str] = None
    # Tool-call fields (only set when type == "tool_call" or "tool_output").
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[str] = None
    # Optional tag preserved from progress events (e.g. "helper_llm_fallback")
    # so the UI can mark fallback replies in history just like live streams.
    reply_via: Optional[str] = None
    # True when a `thinking` entry is NexusPower's own assistant text
    # ("monologue") rather than provider chain-of-thought. The two stream on
    # the same channel but are different tiers: the frontend renders monologue
    # at the "progress" tier and lets CoT recede. Live frames carry the same
    # distinction on `AgentThinking.monologue`; surfacing it here is what keeps
    # a reloaded turn looking like the live one (the equivalence segmentTurn is
    # built on). False for CoT, for every non-NexusPower driver, and for rows
    # persisted before the field existed. Optional like every other
    # type-specific field on this union shape (reply_via, tool_name, …): a
    # tool_call row has no tier, and `None` says that instead of serialising a
    # meaningless `false` on every step of a long log.
    monologue: Optional[bool] = None


class EventLogMeta(BaseModel):
    """Run-level metadata for one activation.

    Drives the activity ("inner thought") card header: what input the agent
    received and from where, what it produced, when the run started, how
    long it took, and what it cost on which models. Sourced from the events
    row (lifecycle Phase C columns) + cost_records aggregation. Every field
    is optional-ish because legacy rows predate the lifecycle columns and
    background helper calls may have no cost rows.
    """
    trigger: str = ""
    trigger_source: str = ""
    # What the agent received (env_context.input), capped server-side so a
    # huge bus payload cannot bloat the response.
    input_text: Optional[str] = None
    final_output: Optional[str] = None
    state: str = "completed"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    # Distinct models that served this event (agent slot + helper + embed).
    models: List[str] = []
    # None (not 0) when no cost rows exist — the UI hides the chip instead
    # of showing a misleading "$0".
    total_cost_usd: Optional[float] = None
    # input_tokens is only the full-rate bucket; on a cache-warm run the two
    # cache buckets below carry the bulk of what the model actually read.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    tool_call_count: int = 0


class EventLogResponse(BaseModel):
    """Response for event log detail endpoint (on-demand loading)"""
    success: bool
    event_id: str = ""
    thinking: Optional[str] = None
    tool_calls: List[EventLogToolCall] = []
    # Ordered, time-preserving view of the same data. The frontend prefers
    # this when present; the legacy thinking / tool_calls fields remain for
    # back-compat with any older client builds still in the wild.
    timeline: List[EventLogTimelineEntry] = []
    # Run-level header info for the activity card; None only on error.
    meta: Optional[EventLogMeta] = None
    error: Optional[str] = None


# ===== File Management Schemas =====

class FileInfo(BaseModel):
    """One node in the agent-workspace directory tree.

    Returned recursively: directories carry ``is_dir=True`` and a ``children``
    list (which may be empty); regular files carry ``is_dir=False`` and
    ``children=None``. Dotfolders (name starts with ``.``) are filtered out
    on the server side and never appear in the tree.
    """
    name: str                          # basename (e.g. "index.html")
    path: str                          # workspace-relative path (e.g. "report/index.html")
    is_dir: bool
    size: int                          # 0 for directories
    modified_at: str
    children: Optional[List["FileInfo"]] = None


# Resolve the self-referential ``children: Optional[List[FileInfo]]``.
FileInfo.model_rebuild()


class FileListResponse(BaseModel):
    """Response for the workspace tree GET. ``tree`` is the top-level node list."""
    success: bool
    tree: List[FileInfo] = []
    workspace_path: str = ""
    error: Optional[str] = None
    # True when the walk hit MAX_TREE_NODES / MAX_TREE_DEPTH (files.py) and
    # the tree is a prefix of the real one (2026-10-28).
    truncated: bool = False


class FileUploadResponse(BaseModel):
    """Response for file upload"""
    success: bool
    filename: Optional[str] = None
    size: Optional[int] = None
    workspace_path: Optional[str] = None
    error: Optional[str] = None


class FileDeleteResponse(BaseModel):
    """Response for file/folder deletion"""
    success: bool
    path: Optional[str] = None
    error: Optional[str] = None


# ===== MCP Schemas =====

class MCPInfo(BaseModel):
    """MCP URL information

    ``headers`` carries MASKED values only (see routes/agents_mcps.py
    ``_mask_header_value``) — plaintext header values never leave the
    backend through read endpoints.
    """
    mcp_id: str
    agent_id: str
    user_id: str
    name: str
    url: str
    headers: Optional[Dict[str, str]] = None
    description: Optional[str] = None
    is_enabled: bool = True
    connection_status: Optional[str] = None
    last_check_time: Optional[str] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MCPListResponse(BaseModel):
    """Response for MCP list"""
    success: bool
    mcps: List[MCPInfo] = []
    count: int = 0
    error: Optional[str] = None


class MCPCreateRequest(BaseModel):
    """Request to create MCP"""
    name: str
    url: str
    headers: Optional[Dict[str, str]] = None
    description: Optional[str] = None
    is_enabled: bool = True


class MCPUpdateRequest(BaseModel):
    """Request to update MCP

    ``headers`` update semantics: field absent → unchanged; field present
    (including ``{}``) → replace the whole header set. The route checks
    ``model_fields_set`` to tell the two apart, so partial merges of
    individual headers are intentionally not supported.
    """
    name: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    description: Optional[str] = None
    is_enabled: Optional[bool] = None


class MCPResponse(BaseModel):
    """Response for single MCP operation"""
    success: bool
    mcp: Optional[MCPInfo] = None
    error: Optional[str] = None


class MCPValidateResponse(BaseModel):
    """Response for MCP validation"""
    success: bool
    mcp_id: str
    connected: bool
    error: Optional[str] = None


class MCPValidateAllResponse(BaseModel):
    """Response for validating all MCPs"""
    success: bool
    results: List[MCPValidateResponse] = []
    total: int = 0
    connected: int = 0
    failed: int = 0
    error: Optional[str] = None


# ===== Job Schemas =====

class JobResponse(BaseModel):
    """
    Response model for a single job.

    v2 timezone protocol (2026-04-21): frontend/UI sees only user-local beta
    fields (next_run_at + next_run_timezone). UTC alpha fields
    (next_run_time, last_run_time) are poller-internal and NOT exposed here.
    """
    job_id: str
    agent_id: str
    user_id: str
    job_type: str
    title: str
    description: Optional[str] = None
    status: str
    payload: Optional[str] = None
    trigger_config: Optional[dict] = None
    process: Optional[List[str]] = None
    next_run_at: Optional[str] = None
    next_run_timezone: Optional[str] = None
    last_run_at: Optional[str] = None
    last_run_timezone: Optional[str] = None
    last_error: Optional[str] = None
    notification_method: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Dependencies (obtained from module_instances table)
    instance_id: Optional[str] = None
    depends_on: List[str] = []
    # Parent narrative (jobs are owned by a narrative; bundle export uses
    # this to group jobs under their narrative for selection).
    narrative_id: Optional[str] = None


class JobListResponse(BaseModel):
    """Response model for job list"""
    success: bool
    jobs: List[JobResponse] = []
    count: int = 0
    error: Optional[str] = None


class JobDetailResponse(BaseModel):
    """Response model for job detail"""
    success: bool
    job: Optional[JobResponse] = None
    error: Optional[str] = None



# ===== Cost Schemas =====
#
# Token fields mirror the ledger's three mutually exclusive input buckets
# (cost_records columns): input_tokens is ONLY the full-rate uncached bucket;
# cache_read_tokens (0.1x) and cache_creation_tokens (1.25x) are separate.
# On a cache-warm agent the cache buckets hold >99% of the input side, so any
# consumer that sums just input+output is off by orders of magnitude — that
# was the popover bug where the helper looked bigger than the main loop.

class CostModelBreakdown(BaseModel):
    """Cost breakdown for a single model"""
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    call_count: int = 0


class CostDailyEntry(BaseModel):
    """Daily token usage entry"""
    date: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


class CostSummary(BaseModel):
    """Aggregated cost summary"""
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    by_model: Dict[str, CostModelBreakdown] = {}
    daily: List[CostDailyEntry] = []


class CostRecord(BaseModel):
    """Single cost record"""
    id: int
    agent_id: str
    event_id: Optional[str] = None
    call_type: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    total_cost_usd: float = 0.0
    created_at: Optional[str] = None


class CostResponse(BaseModel):
    """Response for cost endpoint"""
    success: bool
    summary: Optional[CostSummary] = None
    records: List[CostRecord] = []
    total_count: int = 0
    error: Optional[str] = None
