/**
 * API response type definitions
 */

import type { BusAttachment } from './messages';

/** Base interface for all API responses */
export interface ApiResponse {
  success: boolean;
  error?: string;
  // Some backend envelopes carry a human-readable message alongside a machine
  // error code (e.g. lark unbind: error="no_credential", message="No Lark bot
  // bound to this agent."). Prefer `message` for display when present.
  message?: string;
}

// Job types
export type JobStatus = 'pending' | 'active' | 'running' | 'paused' | 'paused_no_quota' | 'paused_spend_cap' | 'cooling' | 'blocked' | 'blocked_failed' | 'completed' | 'failed' | 'cancelled';
export type JobType = 'one_off' | 'scheduled' | 'ongoing';

// Mirrors backend TriggerConfig (schema/job_schema.py); the list endpoint
// passes trigger_config through verbatim, so field names must match exactly:
// run_at (one_off) / cron / interval_seconds (scheduled) + timezone.
export interface TriggerConfig {
  run_at?: string;              // naive ISO, e.g. "2026-08-01T09:00:00"
  cron?: string;               // cron expression, e.g. "0 8 * * *"
  interval_seconds?: number;
  timezone?: string;           // IANA name, e.g. "Asia/Shanghai"
  end_at?: string;             // scheduled jobs: naive local ISO horizon —
                               // the platform completes the job past this
  end_condition?: string;      // ongoing jobs
  max_iterations?: number;     // ongoing jobs
  [key: string]: unknown;
}

export interface Job {
  job_id: string;
  agent_id: string;
  user_id: string;
  job_type: JobType;
  title: string;
  description?: string;
  status: JobStatus;
  payload?: string;
  trigger_config?: TriggerConfig;
  process?: string[];
  // v2 timezone protocol: user-local naive ISO + IANA pair; no UTC exposure
  next_run_at?: string;
  next_run_timezone?: string;
  last_run_at?: string;
  last_run_timezone?: string;
  last_error?: string;
  notification_method?: string;
  created_at?: string;
  updated_at?: string;
  // Dependencies (fetched from module_instances table)
  instance_id?: string;
  depends_on?: string[];
  // New fields (Feature 2.2, 3.1)
  related_entity_id?: string;      // Target user ID (used as the principal identity during Job execution)
  narrative_id?: string;           // Associated Narrative ID (conversation context)
}

export interface JobListResponse extends ApiResponse {
  jobs: Job[];
  count: number;
}

export interface JobDetailResponse extends ApiResponse {
  job?: Job;
}

export interface CancelJobResponse extends ApiResponse {
  job_id?: string;
  previous_status?: string;
}

// Agent Inbox types (MessageBus channels, room-grouped)

export interface MarkReadResponse extends ApiResponse {
  marked_count: number;
}
export interface RoomMember {
  agent_id: string;
  agent_name: string;
}

export interface RoomMessage {
  message_id: string;
  sender_id: string;
  sender_name: string;
  content: string;
  attachments?: BusAttachment[] | null;
  is_read: boolean;
  created_at?: string;
}

export interface InboxRoom {
  room_id: string;
  room_name: string;
  members: RoomMember[];
  unread_count: number;
  messages: RoomMessage[];
  latest_at?: string;
}

export interface AgentInboxListResponse extends ApiResponse {
  rooms: InboxRoom[];
  total_unread: number;
}

// Bus-failure recovery (upstream #52): messages an agent permanently gave
// up on (retry_count >= 3), plus the user-scope system notices that
// announced them.
export interface BusFailureItem {
  message_id: string;
  channel_id: string;
  from_agent: string;
  content: string;
  retry_count: number;
  last_error: string;
  last_retry_at: string;
  message_created_at: string;
}

export interface BusFailuresResponse extends ApiResponse {
  failures: BusFailureItem[];
}

// Real-time-layer Agent circuit-breaker status (agents_circuit_breaker.py).
// 'probing' (GitHub #117 half-open): exactly one turn is in flight as a
// health-check probe after a PAUSED breaker's delay elapses.
export type CircuitBreakerStatus = 'active' | 'cooling' | 'paused' | 'probing';

export interface AgentCircuitBreakerResponse extends ApiResponse {
  agent_id: string;
  cb_status: CircuitBreakerStatus;
  paused_reason: 'auth' | 'quota' | null;
  consecutive_failure_count: number;
  cooldown_until: string | null;
  last_error: string | null;
}

export interface NoticeItem {
  message_id: string;
  message_type: string;
  title: string;
  content: string;
  is_read: boolean;
  created_at: string;
  source: { type: string; id: string } | null;
}

export interface NoticesResponse extends ApiResponse {
  notices: NoticeItem[];
  unread_count: number;
}

// Awareness types
export interface AwarenessResponse extends ApiResponse {
  awareness?: string;
  create_time?: string;
  update_time?: string;
}

/** Response of POST /api/runs/{run_id}/cancel. */
export interface CancelRunResponse extends ApiResponse {
  run_id: string;
  /** The run's state at the moment the request was recorded. */
  state: string;
  /** True when the run had already finished — nothing was flagged. */
  already_settled: boolean;
  /** How many runs the stop was applied to, this one included. >1 means the
   *  run had caused others and the whole trigger tree was stopped. */
  cascaded?: number;
}

// Clear history types
export interface ClearHistoryResponse extends ApiResponse {
  scopes: string[];
  narrative_ids_deleted: string[];
  narratives_count: number;
  events_count: number;
  event_stream_count: number;
  chat_memory_count: number;
  chat_instances_count: number;
  agent_messages_count: number;
  bus_messages_count: number;
  memory_rows_count: number;
  artifacts_count: number;
  disk_markdown_removed: boolean;
  disk_trajectories_removed: boolean;
  session_removed: boolean;
  disk_errors: string[];
}

// Social Network types
export interface SocialNetworkEntity {
  entity_id: string;
  entity_name?: string;
  aliases?: string[];              // Cross-system IDs and alternate names
  entity_description?: string;
  entity_type: string;
  familiarity?: string;            // "direct" | "known_of"
  identity_info: Record<string, unknown>;
  contact_info: Record<string, unknown>;
  tags: string[];                  // Backward compat
  keywords?: string[];             // Same data, new name
  relationship_strength: number;
  interaction_count: number;
  last_interaction_time?: string;
  // New fields (Feature 2.2, 2.3)
  persona?: string;                // Communication style/characteristics
  related_job_ids?: string[];      // Associated Job IDs
  expertise_domains?: string[];    // Expertise domains
}

export interface SocialNetworkResponse extends ApiResponse {
  entity?: SocialNetworkEntity;
}

export interface SocialNetworkListResponse extends ApiResponse {
  entities: SocialNetworkEntity[];
  count: number;
}

// Semantic search response
export interface SocialNetworkSearchResponse extends ApiResponse {
  entities: Array<SocialNetworkEntity & { similarity_score?: number }>;
  count: number;
  search_type: 'keyword' | 'semantic';
}

// Chat History types
export interface EventLogEntry {
  timestamp: string;
  type: string;
  content: unknown;
}

// Simple Chat History types (for displaying recent interactions)
export interface SimpleChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
  narrative_id?: string;
  working_source?: string;  // "chat" | "job" | "lark" | etc.
  message_type?: string;    // "chat" (default) | "activity"
  event_id?: string;        // Associated Event ID (for loading event_log on demand)
  bootstrap?: boolean;      // The seeded first-run greeting row (dedups the client's folded copy)
  attachments?: import('./messages').Attachment[];  // User uploads attached to this message
}

export interface SimpleChatHistoryResponse extends ApiResponse {
  messages: SimpleChatMessage[];
  total_count: number;
}

// Event Log Detail types (on-demand loading for chat history)
export interface EventLogToolCall {
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_output?: string;
}

// One step in the time-ordered timeline of a historical turn.
// Mirrors backend EventLogTimelineEntry. Preserved as a discriminated
// union of literal-typed entries so the frontend can switch on `type`
// without runtime checks.
export interface EventLogTimelineEntry {
  type: 'thinking' | 'tool_call' | 'tool_output' | 'native_output' | 'reply';
  content?: string;
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  tool_output?: string;
  reply_via?: string;
  /**
   * `thinking` entries only: this block is NexusPower's own narration rather
   * than provider chain-of-thought, so it replays at the PROGRESS tier. The
   * backend already resolved the tier (a mixed block reports false), so this
   * is a plain bool here while the live frame carries the subset text.
   * Absent on rows persisted before the field existed → plain thinking.
   */
  monologue?: boolean;
}

// Mirrors backend EventLogMeta — run-level header for the activity card:
// what the agent received, what it produced, lifecycle and costs. Fields
// are null/empty on legacy rows (pre-lifecycle) and cost-less runs.
export interface EventLogMeta {
  trigger: string;
  trigger_source: string;
  input_text?: string | null;
  final_output?: string | null;
  state: string;
  started_at?: string | null;
  finished_at?: string | null;
  duration_seconds?: number | null;
  models: string[];
  total_cost_usd?: number | null;
  // input_tokens is only the full-rate bucket; the cache buckets carry the
  // bulk of what the model read on a cache-warm run (same split as costs).
  // Optional: absent in responses cached by older builds.
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number;
  cache_creation_tokens?: number;
  tool_call_count: number;
}

export interface EventLogResponse extends ApiResponse {
  event_id: string;
  thinking?: string;
  tool_calls: EventLogToolCall[];
  // Ordered, time-preserving view; the UI prefers this when present and
  // falls back to (thinking, tool_calls) only for old backends that
  // haven't been redeployed yet.
  timeline?: EventLogTimelineEntry[];
  // Run-level header info; absent on old backends.
  meta?: EventLogMeta | null;
}

export interface ChatHistoryEvent {
  event_id: string;
  narrative_id?: string;
  narrative_name?: string;
  trigger: string;
  trigger_source: string;
  user_id?: string;
  final_output: string;
  created_at: string;
  event_log: EventLogEntry[];
}

// Module Instance info for displaying in Narrative
export interface InstanceInfo {
  instance_id: string;
  module_class: string;
  description: string;
  status: string;
  dependencies: string[];
  config: Record<string, unknown>;
  created_at?: string;
  user_id?: string;  // Used to filter events by user_id
}

export interface ChatHistoryNarrative {
  narrative_id: string;
  name: string;
  description: string;
  current_summary: string;
  actors: Array<{ id: string; type: string }>;
  created_at: string;
  updated_at: string;
  instances: InstanceInfo[];  // Associated Module Instances
}

export interface ChatHistoryResponse extends ApiResponse {
  narratives: ChatHistoryNarrative[];
  events: ChatHistoryEvent[];
  narrative_count: number;
  event_count: number;
}

// Create Agent types
export interface CreateAgentRequest {
  agent_name?: string;
  agent_description?: string;
  created_by: string;
  // #43: optional team to attach the new agent to on creation.
  team_id?: string;
}

/**
 * Phase C: summary of an agent's currently running run, if any.
 * Returned alongside AgentInfo when GET /api/auth/agents fires. The
 * UI renders this to surface "this agent is still working" even
 * across browser tabs / sessions — see iron rule #14 (agent runs are
 * first-class and outlive any single WebSocket).
 */
export interface ActiveRunInfo {
  run_id: string;
  state: 'running' | 'cancelling' | 'completed' | 'cancelled' | 'failed';
  started_at?: string;
  last_event_at?: string;
  tool_call_count: number;
  current_stage?: string;
}

/** One channel an owned agent is bound to. `active` false = configured but
 *  switched off (the credential's enable switch is 0). */
export interface BoundChannel {
  channel: string;
  active: boolean;
}

export interface AgentInfo {
  agent_id: string;
  name?: string;
  description?: string;
  status?: string;
  created_at?: string;
  is_public?: boolean;
  created_by?: string;
  /**
   * Effective agent-slot runtime identity, resolved server-side through the
   * same overlay the runtime dispatches on (a rebinding per-agent override,
   * else the owner's default, else the platform default). Present for the
   * viewer's OWN agents only; absent for someone else's public agent, and
   * the UI renders absent as '—' rather than inventing a brand.
   */
  agent_framework?: string;
  model?: string;
  /**
   * Channels bound to this agent, each with its on/off state. Always [] for a
   * public agent owned by someone else — another user's integrations are not
   * the viewer's business.
   */
  bound_channels: BoundChannel[];
  bootstrap_active?: boolean;
  /** Per-agent first-run greeting (Arena etc.); falls back to the generic
   *  constant when absent. Must match the DB-persisted greeting so the
   *  instant frontend bubble and the persisted one don't duplicate. */
  bootstrap_greeting?: string;
  /**
   * Set when the backend has a BackgroundRun task in the running state
   * for this agent + the current user. Null means "not currently running".
   */
  active_run?: ActiveRunInfo | null;
  /**
   * NM sidebar preview — most recent persisted assistant reply for this
   * agent, server-truncated to ~200 chars. Lets the sidebar show "what
   * did this agent last say" on rows the user has not opened this
   * session. Falls back to the local chat session when the live stream
   * just produced a fresher reply that has not yet been re-fetched.
   */
  last_assistant_preview?: string | null;
  last_assistant_at?: string | null;
}

// Auth types
export interface LoginResponse extends ApiResponse {
  user_id?: string;
  token?: string;  // JWT token (cloud mode)
  role?: string;   // 'user' | 'staff' (cloud mode)
}

// Response from /api/auth/netmind-login (cloud NetMind account login).
export interface NetmindLoginResponse extends ApiResponse {
  user_id?: string;
  token?: string;        // our self-issued JWT
  role?: string;
  is_new_user?: boolean;
  // Whether the deployment auto-provisions the onboarding guide agent —
  // gates the "your first agent is already here" coachmark so the server's
  // kill-switch silences the UI too.
  guide_agent_provisioning?: boolean;
  display_name?: string;
  email?: string;
}

// Response shape for GET /api/quota/me. Discriminated by `enabled` and
// `status` so the UI can switch exhaustively without "is the feature on"
// booleans scattered through the component tree.
//
// The free tier is a USD wallet on the LLM gateway (not a token counter), and
// it is registered as an ordinary provider card — so there is no "your model
// choice is ignored while the free tier lasts" lock any more. Users on the free
// tier switch models like anyone else; only the balance is special.
export type QuotaMeResponse =
  | { enabled: false }
  | { enabled: true; status: 'uninitialized' }
  | {
      enabled: true;
      status: 'active' | 'exhausted';
      currency: string;
      max_budget: number;
      spend: number;
      remaining: number;
    };

export interface CreateUserResponse extends ApiResponse {
  user_id?: string;
  // See NetmindLoginResponse.guide_agent_provisioning.
  guide_agent_provisioning?: boolean;
}

export interface AgentListResponse extends ApiResponse {
  agents: AgentInfo[];
  count: number;
}

/** GET /api/auth/session — "is my session still alive, and until when?".
 *  `expires_at` / `issued_at` are epoch SECONDS, null in local mode where
 *  identity is a header and there is nothing to expire. */
export interface SessionResponse {
  user_id: string;
  expires_at: number | null;
  issued_at: number | null;
}

export interface UpdateTimezoneResponse extends ApiResponse {
  timezone?: string;
}

/** New-user onboarding checklist state. Mirrors backend OnboardingProgress —
 *  three write-once-true flags persisted in users.metadata. */
export interface OnboardingProgress {
  first_agent_created: boolean;
  template_applied: boolean;
  dismissed: boolean;
  /** The one-time first-run flow (WelcomePage) has been seen — finished OR
   *  skipped. Server-side so another browser/machine doesn't replay it. */
  landing_completed: boolean;
}

export interface OnboardingResponse extends ApiResponse {
  progress?: OnboardingProgress;
}

export interface CreateAgentResponse extends ApiResponse {
  agent?: AgentInfo;
}

export interface UpdateAgentRequest {
  agent_name?: string;
  agent_description?: string;
}

export interface UpdateAgentResponse extends ApiResponse {
  agent?: AgentInfo;
  /**
   * agent_id of another agent of the same owner that already answers to the
   * name just applied. NOT an error — handing a name from one agent to another
   * is something owners do deliberately, so the rename IS applied; doing it
   * silently is what let two agents answer to one name (Shenzhen P1).
   * Optional and nullable: older backends omit it entirely.
   */
  name_clash_with?: string | null;
  /**
   * Did this agent's identity memory follow the rename? `null` when the call
   * had nothing to do about it (renamed nothing, found nothing stale). `false`
   * means the name IS stored but the memory was not corrected — the exact state
   * where the agent keeps introducing itself by the old name, so the UI must
   * not report a plain success.
   */
  identity_record_updated?: boolean | null;
}

export interface DeleteAgentResponse extends ApiResponse {
  agent_id?: string;
  deleted_counts?: Record<string, number>;
}

// File Management types — recursive workspace directory tree (2026-05-14).
export interface FileInfo {
  /** Basename, e.g. "index.html". */
  name: string;
  /** Workspace-relative path, e.g. "report/index.html". */
  path: string;
  is_dir: boolean;
  /** 0 for directories. */
  size: number;
  modified_at: string;
  /** Populated when `is_dir` is true; `null` for regular files. */
  children?: FileInfo[] | null;
}

export interface FileListResponse extends ApiResponse {
  tree: FileInfo[];
  workspace_path: string;
  /** True when the backend's node/depth cap left visible entries out. */
  truncated?: boolean;
}

export interface FileUploadResponse extends ApiResponse {
  filename?: string;
  size?: number;
  workspace_path?: string;
}

export interface FileDeleteResponse extends ApiResponse {
  path?: string;
}

// MCP Management types
export interface MCPInfo {
  mcp_id: string;
  agent_id: string;
  user_id: string;
  name: string;
  url: string;
  /** Masked header values (e.g. "Bearer…7890") — plaintext never leaves the backend. */
  headers?: Record<string, string> | null;
  description?: string;
  is_enabled: boolean;
  connection_status?: 'connected' | 'failed' | 'unknown' | null;
  last_check_time?: string;
  last_error?: string;
  created_at?: string;
  updated_at?: string;
}

export interface MCPListResponse extends ApiResponse {
  mcps: MCPInfo[];
  count: number;
}

export interface MCPCreateRequest {
  name: string;
  url: string;
  /** Custom HTTP headers sent on every request to this MCP (e.g. Authorization). */
  headers?: Record<string, string>;
  description?: string;
  is_enabled?: boolean;
}

export interface MCPUpdateRequest {
  name?: string;
  url?: string;
  /** Present (even {}) replaces the whole header set; absent leaves it unchanged. */
  headers?: Record<string, string>;
  description?: string;
  is_enabled?: boolean;
}

export interface MCPResponse extends ApiResponse {
  mcp?: MCPInfo;
}

export interface MCPValidateResponse extends ApiResponse {
  mcp_id: string;
  connected: boolean;
}

export interface MCPValidateAllResponse extends ApiResponse {
  results: MCPValidateResponse[];
  total: number;
  connected: number;
  failed: number;
}

// Cost types
// Token fields mirror the ledger's three mutually exclusive input buckets:
// input_tokens is ONLY the full-rate uncached bucket; cache_read_tokens and
// cache_creation_tokens are separate. On a cache-warm agent the cache buckets
// hold >99% of the input side — display code must sum all three or it
// understates input by orders of magnitude.
// The cache fields are optional: a response served by a backend build that
// predates them has no such keys, and summing undefined renders "NaN".
// Consumers must `?? 0`.
export interface CostModelBreakdown {
  cost: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number;
  cache_creation_tokens?: number;
  call_count: number;
}

export interface CostDailyEntry {
  date: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number;
  cache_creation_tokens?: number;
}

export interface CostSummary {
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read_tokens?: number;
  total_cache_creation_tokens?: number;
  by_model: Record<string, CostModelBreakdown>;
  daily: CostDailyEntry[];
}

export interface CostRecord {
  id: number;
  agent_id: string;
  event_id?: string;
  call_type: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number;
  cache_creation_tokens?: number;
  total_cost_usd: number;
  created_at?: string;
}

export interface CostResponse extends ApiResponse {
  summary?: CostSummary;
  records: CostRecord[];
  total_count: number;
}

// ---------------------------------------------------------------------------
// Dashboard v2 types (T19)
//
// Discriminated union via `owned_by_viewer`. Public-variant lacks owner-only
// fields at the type level — TS users cannot accidentally read sessions or
// action_line on a public agent.
// ---------------------------------------------------------------------------

export type AgentKind =
  | 'idle'
  | 'CHAT'
  | 'JOB'
  | 'MESSAGE_BUS'
  | 'A2A'
  | 'CALLBACK'
  | 'SKILL_STUDY'
  // an IM channel session — the channel's working source upper-cased ('LARK', 'MATRIX', a plugin's 'ACME_CHAT')
  | Uppercase<string>;

export interface MessageBusDetails {
  src_channel?: string | null;
  dst_channel?: string | null;
}

export interface StatusCommon {
  kind: AgentKind;
  last_activity_at: string | null;
  started_at: string | null;
}

export interface StatusWithDetails extends StatusCommon {
  details?: MessageBusDetails | null;
}

export interface JobProgress {
  current_step: number;
  total_steps: number;
  stage_name?: string | null;
  estimated_pct?: number | null;
}

export type JobQueueStatus = 'pending' | 'active' | 'blocked' | 'paused' | 'failed' | 'cooling' | 'paused_no_quota' | 'blocked_failed' | 'paused_spend_cap';

export interface SessionInfoResp {
  session_id: string;
  user_display: string;
  channel: string;
  started_at: string;
  /** v2.1: preview of latest user input in this session */
  user_last_message_preview?: string | null;
}

export interface DashboardRunningJob {
  job_id: string;
  title: string;
  job_type: string;
  started_at: string | null;
  /** v2.1 */
  description?: string | null;
  progress?: JobProgress | null;
}

export interface DashboardPendingJob {
  job_id: string;
  title: string;
  job_type: string;
  next_run_at: string | null;
  next_run_timezone: string | null;
  /** v2.1 */
  description?: string | null;
  /** v2.1: which live state this queued job is in */
  queue_status?: JobQueueStatus;
}

export interface EnhancedSignals {
  recent_errors_1h: number;
  token_rate_1h: number | null;
  active_narratives: number;
  unread_bus_messages: number;
}

// v2.1 — rich card types

export interface QueueCounts {
  running: number;
  active: number;
  pending: number;
  blocked: number;
  paused: number;
  failed: number;
  cooling: number;
  paused_no_quota: number;
  blocked_failed: number;
  paused_spend_cap: number;
  total: number;
}

export type RecentEventKind = 'completed' | 'running' | 'failed' | 'chat' | 'other';

export interface RecentEvent {
  event_id: string;
  kind: RecentEventKind;
  verb: string;
  target?: string | null;
  duration_ms?: number | null;
  created_at: string;
}

export type MetricsTrend = 'up' | 'down' | 'flat' | 'unknown';

export interface MetricsToday {
  runs_ok: number;
  errors: number;
  avg_duration_ms: number | null;
  avg_duration_trend: MetricsTrend;
  token_cost_cents: number | null;
}

export interface AttentionBannerAction {
  label: string;
  endpoint: string;
  method?: 'POST' | 'GET';
}

export type AttentionBannerKind =
  | 'job_failed'
  | 'job_blocked'
  | 'jobs_paused'
  | 'slow_response';

export type AttentionBannerLevel = 'error' | 'warning' | 'info';

export interface AttentionBanner {
  level: AttentionBannerLevel;
  kind: AttentionBannerKind;
  message: string;
  action?: AttentionBannerAction | null;
}

export type AgentHealth =
  | 'healthy_running'
  | 'healthy_idle'
  | 'idle_long'
  | 'warning'
  | 'error'
  | 'paused'
  | 'acknowledged'; // v2.2 G2: error fully-dismissed visual (slate rail + red ack dot)

/** v2.2 G3: a module instance stuck in_progress past the stale threshold. */
export interface StaleInstance {
  instance_id: string;
  module_class: string;
  description: string | null;
}

export interface OwnedAgentStatus {
  agent_id: string;
  name: string;
  description: string | null;
  is_public: boolean;
  owned_by_viewer: true;
  status: StatusWithDetails;
  running_count: number;
  /** null → frontend must render "—" */
  action_line: string | null;
  /** v2.1: human verb ("Serving 3 users" / "Running: weekly-report" / "Idle · last active 4m ago") */
  verb_line: string | null;
  sessions: SessionInfoResp[];
  running_jobs: DashboardRunningJob[];
  pending_jobs: DashboardPendingJob[];
  enhanced: EnhancedSignals;
  // v2.1 rich fields
  queue: QueueCounts;
  recent_events: RecentEvent[];
  metrics_today: MetricsToday;
  attention_banners: AttentionBanner[];
  health: AgentHealth;
  // v2.2 G3: zombie module instances (in_progress past stale threshold)
  stale_instances: StaleInstance[];
}

export interface PublicAgentStatus {
  agent_id: string;
  name: string;
  description: string | null;
  is_public: true;
  owned_by_viewer: false;
  status: StatusCommon;
  running_count_bucket: '0' | '1-2' | '3-5' | '6-10' | '10+';
}

export type AgentStatus = OwnedAgentStatus | PublicAgentStatus;

export interface DashboardResponse extends ApiResponse {
  agents: AgentStatus[];
}

// Lark / Feishu Integration types
//
// The credential views below are the generic channel store's public half
// (`/api/channels/<channel>/credential`): identity fields plus `enabled`;
// secrets never leave the server.
export interface LarkCredentialData {
  agent_id: string;
  app_id: string;
  brand: string;
  bot_name: string;
  owner_open_id: string;
  owner_name: string;
  auth_status: string;
  enabled: boolean;
}


/**
 * Structured Lark/Feishu bind failure — translator-rendered.
 *
 * Mirrors `_lark_error_translator.ErrorTranslation` on the backend. Allows the
 * frontend to render a "title + explanation + actionable hint + clickable
 * console link" card instead of dumping raw lark-cli stderr into a red div.
 * Present on bind / re-bind responses when `success: false` AND the backend
 * recognised the error class. Absent on legacy paths or unknown errors —
 * frontend should fall back to the plain `error` string in that case.
 */
export interface LarkErrorDetail {
  code: string;
  severity: 'error' | 'warning' | 'info' | string;
  title: string;
  message: string;
  action_hint: string;
  console_url: string;
  raw_message: string;
}

/**
 * Non-blocking observation surfaced after a successful bind — e.g. an
 * optional scope is missing, or the event-subscription probe couldn't
 * confirm WS delivery within its timeout. UI renders as a yellow
 * callout so the user knows what to fix later, without blocking bind.
 */
export interface LarkBindWarning {
  kind: string;        // 'scope_optional_missing' | 'event_probe_<kind>' | ...
  severity: 'warning' | 'info' | string;
  title: string;
  message: string;
  raw_error?: string;
}

export interface LarkBindResponse extends ApiResponse {
  data?: {
    profile_name: string;
    brand: string;
    app_id: string;
    auth_status: string;
    owner_open_id: string;
    owner_name: string;
  };
  error_detail?: LarkErrorDetail;
  warnings?: LarkBindWarning[];
}

export interface LarkAuthLoginResponse extends ApiResponse {
  data?: {
    verification_url?: string;
    verification_uri?: string;
    device_code?: string;
    user_code?: string;
  };
}

export interface LarkAuthCompleteResponse extends ApiResponse {
  data?: Record<string, unknown>;
}

// Slack Integration types
//
// Note: bot_token / app_token are NEVER returned by the API. The backend
// responds only with this sanitised view, which is everything the UI
// needs to render binding state.
export interface SlackCredentialData {
  agent_id: string;
  bot_user_id: string;
  team_id: string;
  team_name: string;
  owner_email: string;
  owner_user_id: string;
  owner_name: string;
  enabled: boolean;
  /** Why the platform switched the binding off (permanent upstream failure); "" while enabled. */
  disabled_reason?: string;
}


export interface SlackBindResponse extends ApiResponse {
  data?: {
    team_id: string;
    team_name: string;
    bot_user_id: string;
    owner_user_id: string;
    owner_name: string;
  };
}

export interface SlackTestResponse extends ApiResponse {
  data?: {
    team_id: string;
    team_name: string;
    bot_user_id: string;
    bot_name?: string;
  };
}

// Telegram Integration types
//
// Note: bot_token is NEVER returned by the API. The backend returns only
// this sanitised view, which is everything the UI needs to render binding
// state.
export interface TelegramCredentialData {
  agent_id: string;
  bot_user_id: string;
  bot_username: string;
  owner_username: string;
  owner_user_id: string;
  owner_name: string;
  enabled: boolean;
  /** Why the platform switched the binding off (revoked token, competing poller); "" while enabled. */
  disabled_reason?: string;
}


export interface NarramessengerCredentialData {
  agent_id: string;
  backend_base_url: string;
  matrix_homeserver_url: string;
  matrix_user_id: string;
  nexus_principal_id: string;
  nexus_profile_id: string;
  bind_room_id: string;
  owner_matrix_user_id: string;
  owner_name: string;
  connection_mode: string;
  enabled: boolean;
  /** Why the platform switched the binding off (permanent upstream failure); "" while enabled. */
  disabled_reason?: string;
}


export interface NarramessengerBindResponse extends ApiResponse {
  data?: {
    matrix_user_id: string;
    principal_id: string;
    room_id: string;
    connection_mode: string;
  };
}

export interface TelegramBindResponse extends ApiResponse {
  data?: {
    bot_user_id: string;
    bot_username: string;
    owner_user_id: string;
    owner_name: string;
  };
}

// WeChat (iLink) Integration types
//
// Personal WeChat binds via a QR-scan flow, not a token paste. The bot_token
// is NEVER returned by the API — only this sanitised view (mirrors the
// backend's WeChatCredential.to_public_dict). The owner_wx_id / bot_wx_id are
// opaque until the first inbound DM claims ownership, so they may be empty
// right after binding.
export interface WeChatCredentialData {
  agent_id: string;
  base_url: string;
  bot_wx_id: string;
  owner_wx_id: string;
  owner_user_id: string;
  owner_name: string;
  enabled: boolean;
  /** Why the platform switched the binding off (permanent upstream failure); "" while enabled. */
  disabled_reason?: string;
}


// /qrcode/start → the login QR. `qr_url` is a WeChat URL the user scans;
// `qrcode` is the opaque handle passed back to /qrcode/poll. `base_url` is
// the per-account gateway URL when the gateway issued one at QR time.
export interface WeChatQrStartResponse extends ApiResponse {
  data?: {
    qrcode: string;
    qr_url: string;
    base_url?: string;
  };
}

// /qrcode/poll → scan status. `wait` = keep polling; `confirmed` = bound.
export interface WeChatQrPollResponse extends ApiResponse {
  data?: {
    status: 'wait' | 'confirmed';
  };
}

export interface TelegramTestResponse extends ApiResponse {
  data?: {
    bot_user_id: string;
    bot_username: string;
    first_name?: string;
  };
}

// Discord Integration types
//
// Note: bot_token is NEVER returned by the API. The backend returns only
// this sanitised view, which is everything the UI needs to render binding
// state.
export interface DiscordCredentialData {
  agent_id: string;
  bot_user_id: string;
  bot_username: string;
  owner_user_id: string;
  owner_name: string;
  enabled: boolean;
  /** Why the platform switched the binding off (permanent upstream failure); "" while enabled. */
  disabled_reason?: string;
}


export interface DiscordBindResponse extends ApiResponse {
  data?: {
    bot_user_id: string;
    bot_username: string;
    owner_user_id: string;
    owner_name: string;
  };
}

export interface DiscordTestResponse extends ApiResponse {
  data?: {
    bot_user_id: string;
    bot_username: string;
  };
}

// =============================================================================
// NetMind billing / subscription (Phase 1: account status panel)
// Field shapes verified against dev 2026-07-02 live probe — do NOT hardcode
// rpm/period values (dev drifts: Free rpm=60, Pro period="2day").
// =============================================================================

export interface SubscriptionPlanPrice {
  period: string; // e.g. "month" / "2day" (env-dependent)
  currency: string;
  stripe_price_id: string;
}

// Verbatim NetMind proxy (like FeeInfo): no backend schema validation, so treat
// every nested field as possibly absent at runtime and read defensively.
export interface SubscriptionPlan {
  plan_id: string; // "free" | "pro"
  name: string;
  quota_limits: { rpm?: number };
  features: { support?: boolean; member_price?: boolean };
  monthly_grant_usd: number;
  // What a month COSTS, as opposed to monthly_grant_usd (what it gives you).
  // They happen to be equal today; the one-time purchase total must be built
  // from the price, because a change to either would otherwise silently
  // mis-price a 12-month checkout. Nullable upstream on the free plan.
  usd_monthly_price?: number | null;
  prices: SubscriptionPlanPrice[];
}

export interface SubscriptionStatus {
  subscription_id: string;
  status: string; // e.g. "ACTIVE"
  current_period_start: number; // Unix seconds
  current_period_end: number; // Unix seconds
  auto_renew: boolean;
  // Which product this subscription actually is. Load-bearing, not decorative:
  // a one-time (Alipay/WeChat) purchase never renews, so `auto_renew` is false
  // for its whole life — the exact tuple a CANCELLED card subscription sits in.
  // Every other field reads identically in the two states, so this is what the
  // panel branches on — and the two need opposite actions (resume auto-renew
  // vs buy more months).
  //
  // Optional because every subscription created before the nexus account is a
  // card one and predates the field; absent therefore means "card", which is
  // what the pre-existing reading already assumed — and what keeps the
  // branching correct whether upstream spells the card case "stripe" or omits
  // it entirely. Only the one-time shape was measured (dev 2026-08-19, a live
  // Alipay purchase); the card case is inferred, which is why the fallback
  // rather than an equality test is what the code relies on.
  payment_method?: SubscribePaymentMethod;
}

// GET /api/billing/subscription -> data. Plan fields are flat at top level;
// `subscription` is null when the user is on Free.
export interface SubscriptionMe extends SubscriptionPlan {
  subscription: SubscriptionStatus | null;
}

export interface PlanList {
  plans: SubscriptionPlan[];
}

export interface SubscriptionMeResponse extends ApiResponse {
  data?: SubscriptionMe;
}

export interface PlanListResponse extends ApiResponse {
  data?: PlanList;
}

// Which Pro product to start. `stripe` is a card subscription that renews
// itself; `alipay` / `wechat` cannot pay a Stripe subscription at all and are
// therefore a ONE-TIME purchase of N months that ends when the period does.
export type SubscribePaymentMethod = 'stripe' | 'alipay' | 'wechat';

// POST /api/billing/subscribe -> Stripe checkout to redirect the user to.
export interface SubscribeCheckout {
  session_id: string;
  checkout_url: string;
  // Only on a CNY (WeChat) one-time purchase; see RechargeCheckout.
  charge_currency?: string;
  charge_amount?: string;
  fx_rate?: string | null;
}

export interface SubscribeResponse extends ApiResponse {
  data?: SubscribeCheckout;
}

// POST /api/billing/cancel | /reactivate -> small status envelope
// (cancel: { status: "auto_renew_off" }; reactivate shape TBD).
export interface BillingActionResponse extends ApiResponse {
  data?: Record<string, unknown>;
}

// GET /api/billing/fee-info -> user balance + eligibility (module B).
// Amounts are strings (USD). NOTE (G1): no per-period consumption field, and
// `free_credit` conflates subscription grant + recharge — degraded display.
//
// Every field is OPTIONAL: the backend proxies NetMind's JSON verbatim (no
// schema validation), so a partial/misshapen 200 is possible. Call sites MUST
// use optional chaining + fallbacks — never assume a field is present, or a
// malformed-but-200 response crashes the render.
export interface FeeInfo {
  user_id?: string;
  eligible?: boolean;
  checks?: {
    has_arrears?: boolean;
    card_within_limit?: boolean;
    has_bound_card?: boolean;
  };
  metrics?: {
    balance?: { usd?: string; nmt?: string; cny?: string };
    free_credit?: string; // current balance (recharge + subscription grant + activity)
    // Remaining Pro subscription grant, listed separately (2026-07-18). Grants
    // ACCUMULATE across cycles (verified on dev: 3 × $19 cycles ≈ 56.98), so
    // the panel splits it into "this cycle's tank" + overflow (into balance).
    subscription_credit?: string;
    // CAUTION: observed 0.50 on dev while the real per-cycle grant is $19 —
    // semantics unverified; do NOT use as the cycle denominator (the panel
    // uses proPlan.monthly_grant_usd instead).
    monthly_free_credit?: string;
    arrears?: { pending_bills_count?: number; pending_payments_count?: number };
    card_month?: {
      spent_usd?: string;
      limit_usd?: string | null;
      remaining_usd?: string | null;
    };
  };
}

export interface FeeInfoResponse extends ApiResponse {
  data?: FeeInfo;
}

// GET /api/billing/records -> financial transactions (module B activity, G1).
// Amounts are strings. direction: "expense" (consumption) / "income".
export interface FinanceRecord {
  kind: string; // Payment / Recharge / Refund / Withdraw
  record_id: string;
  created_at: string; // ISO-8601 UTC
  amount: string;
  currency: string;
  product?: string;
  method?: string; // free_credit / stripe / card
  status: string; // succeeded / pending / failed
  type: string;
  direction: string; // "expense" | "income"
}

export interface RecordsResponse extends ApiResponse {
  data?: FinanceRecord[];
  has_next?: boolean;
}

// Which rail the payer picked. `default` is the card page (Stripe shows Alipay
// alongside the card form there too); the explicit values force one rail.
// WeChat is the only one that settles in a foreign currency — see FxQuote.
export type RechargePaymentMethod = 'default' | 'alipay' | 'wechat';

// POST /api/billing/recharge -> hosted Stripe checkout for a top-up (module E).
export interface RechargeCheckout {
  recharge_id?: string;
  session_id: string;
  checkout_url: string;
  status?: string; // pending at creation
  // Present only when the charge is not in USD (WeChat). `charge_amount` is
  // what the payer's bank actually takes; the credit they receive is still the
  // USD `amount` they asked for.
  charge_currency?: string;
  charge_amount?: string;
  fx_rate?: string | null;
}

// GET /api/billing/fx-rate -> what a CNY charge would cost right now.
//
// Every field is a STRING (upstream sends money as text to avoid float drift)
// and every field is optional: the backend proxies the upstream body without
// schema validation, so a partial 200 is possible and call sites must not
// assume presence — the same rule FeeInfo carries.
export interface FxQuote {
  from?: string;
  to?: string;
  rate?: string;
  amount_usd?: string;
  charge_amount?: string;
  // The floor below which upstream rejects the payment outright. Shown and
  // enforced before checkout so the user never meets that 400.
  min_amount_usd?: string;
  min_charge?: string;
}

export interface FxQuoteResponse extends ApiResponse {
  data?: FxQuote;
}

export interface RechargeResponse extends ApiResponse {
  data?: RechargeCheckout;
}

// GET /api/billing/recharge/{session_id} -> poll status by Stripe session.
export interface RechargeStatus {
  recharge_id?: string;
  session_id?: string;
  status?: string; // pending | succeeded | failed
  amount?: string;
  currency?: string;
}

export interface RechargeStatusResponse extends ApiResponse {
  data?: RechargeStatus;
}

// ── per-agent LLM config (GET /api/agents/{id}/llm-config) ────────────────

/** The flat, effective config for one slot (override or owner default). */
export interface AgentSlotEffective {
  provider_id: string;
  model: string;
  thinking: string;
  reasoning_effort: string;
  // Present only on the 'agent' slot.
  agent_framework?: string;
}

/** Per-slot view: whether the agent inherits the owner default, the effective
 *  config, and (if any) the raw override + owner default. */
export interface AgentSlotView {
  inheriting: boolean;
  effective: AgentSlotEffective | null;
  override: AgentSlotEffective | null;
  owner_default: AgentSlotEffective | null;
}

// ── owner-scoped bulk slot ops (Model Defaults "apply to all agents") ──────
export interface SlotOverrideStats {
  agent: number;
  helper_llm: number;
  total_agents: number;
}

// ── local-only plugin installs (Settings › Plugins) ────────────────────────
// Claude Code / Codex CLI are user-installed local plugins rather than
// baked into the desktop image, so the frontend has to show install state
// and drive a pip/npm install through the backend orchestrator.
export type PluginId = 'claude_code' | 'codex_cli';

export interface PluginStatus {
  id: PluginId;
  display_name: string;
  installed: boolean;
  version: string | null;
  target_version: string;
  update_available: boolean;
  logged_in: boolean;
  size_hint: string;
  /** An install/uninstall is in flight for this plugin right now. */
  busy: boolean;
}

export interface PluginsListResponse extends ApiResponse {
  data?: {
    plugins: PluginStatus[];
    /** Cloud deployments manage plugins centrally — the panel hides itself. */
    cloud_managed: boolean;
  };
}

export interface PluginUninstallResponse extends ApiResponse {
  data?: PluginStatus;
}

// ---------------------------------------------------------------- plugin factory
// `/api/plugin-factory` — user plugins (GitHub / local), managed by the kernel
// registry file. Distinct from the framework installer above.

export type FactoryPluginState =
  | 'registered' | 'validated' | 'enabled' | 'active'
  | 'incompatible' | 'blocked' | 'missing' | 'deps_missing' | 'crashed' | 'disabled' | 'slow';

export interface FactoryPlugin {
  id: string;
  display_name: string;
  description: string;
  version: string;
  mode: 'copy' | 'link';
  path: string;
  source: { type: 'github' | 'github_repo' | 'local'; repo?: string; tag?: string; ref?: string; commit?: string };
  enabled: boolean;
  state: FactoryPluginState;
  scope: string;
  last_error: string | null;
  crash_count: number;
  warnings: string[];
  permissions: { network?: string[]; filesystem?: string[]; subprocess?: boolean; env?: string[] };
  permissions_acknowledged: boolean;
  installed_by: string;
  installed_at: string;
  provides: string[];
  size: { backend_deps_mb?: number; frontend_kb?: number };
  loaded: boolean;
  isolated: string | null;
  recent_errors: number;
  frontend: { entry: string; integrity?: string } | null;
  activation_events: string[];
  protected: boolean;
}

export interface FactoryBisect {
  candidates: string[];
  trial: string[];
  cleared: string[];
}

/** GET /api/channels/{channel}/schema — the generic bind form of a channel (plugin platform batch 4b). */
/** One row of `GET /api/plugins/channels` — the host's `ingress.channels` registry as data. */
export interface PluginChannelRow {
  name: string;
  display_name?: string;
  owner: string;
  ui?: { label?: string; icon?: string; order?: number };
}

export interface ChannelSchemaField {
  name: string;
  kind: 'string' | 'secret' | 'url' | 'bool' | 'int' | 'select';
  label: string;
  help: string;
  required: boolean;
  options: string[];
  public: boolean;
}

export interface ChannelSchema {
  channel: string;
  display_name: string;
  transport: 'socket' | 'poll' | 'webhook' | 'none';
  has_bind: boolean;
  has_test: boolean;
  manager_backed: boolean;
  /** The stored credential shape (identity fields shown back after bind). */
  fields: ChannelSchemaField[];
  /** What a bind call takes — differs from `fields` for builtin channels whose service binds from e.g. a pasted link. */
  bind_fields: ChannelSchemaField[];
  external_id_field: string;
}

/** One registered module as the owner's capability panel sees it (plugin platform batch 5c). */
export interface AgentCapabilityItem {
  module_class: string;
  name: string;
  icon: string;
  description: string;
  owner: string;
  builtin: boolean;
  enabled: boolean;
  default_enabled: boolean;
  explicit: boolean;
  locked: boolean;
  always_load: boolean;
  context_cost_hint: number | null;
  priority: number;
}

export interface AgentCapabilitiesView {
  agent_id: string;
  capabilities: AgentCapabilityItem[];
  budget: { baseline_tokens: number; enabled_tokens: number; ratio: number; over_budget: boolean };
}

/** The public half of a generic channel credential (identity fields only; secrets never leave the server). */
export interface ChannelCredentialView {
  channel: string;
  agent_id: string;
  enabled: boolean;
  external_id: string | null;
  /** Why the platform switched the binding off (permanent upstream failure); "" while enabled. */
  disabled_reason?: string;
  [field: string]: unknown;
}

export interface FactoryBuiltin {
  id: string;
  display_name: string;
  description: string;
  version: string;
  enabled: boolean;
  /** The running distribution leaves this builtin out: it is never loaded, so `enabled` is false and cannot be changed. */
  excluded: boolean;
  protected: boolean;
  hosts: string[];
  provides: string[];
  dependencies: Record<string, string>;
  /** Declares heavy dependencies installed on first boot of a slim build. */
  on_demand?: boolean;
  pip?: string[];
  /** Why the last boot ran without this builtin (its on-demand dependencies are unavailable); null when fine. */
  deps_missing?: string | null;
}

export interface FactoryListResponse extends ApiResponse {
  data?: {
    plugins: FactoryPlugin[];
    /** Builtin (feature-level) plugins with their enabled flag from registry.json builtin_overrides. */
    builtins?: FactoryBuiltin[];
    safe_mode: boolean;
    safe_mode_reason: string;
    bisect: FactoryBisect | null;
    cloud_managed: boolean;
    boot: { role: string; loaded: string[]; isolated: Record<string, string>; rejected: Record<string, string>; duration_ms: number } | null;
  };
}

export interface FactoryInstallResponse extends ApiResponse {
  data?: {
    id: string;
    version: string;
    path: string;
    mode: 'copy' | 'link';
    warnings: string[];
    deps_installed: string[];
    permissions: FactoryPlugin['permissions'];
    restart_required: boolean;
  };
}

export interface FactoryProposal {
  id: string;
  plugin_id: string;
  agent_id: string;
  user_id: string;
  action: 'activate' | 'install' | 'upgrade' | 'deactivate';
  scope: string;
  summary: string;
  permissions: FactoryPlugin['permissions'];
  test_report: { ok?: boolean; passed?: number; report_hash?: string };
  diff_hash: string;
  created_at: number;
  decision: 'pending' | 'approved' | 'rejected' | 'expired';
  extra: Record<string, unknown>;
}

export interface FactoryProposalsResponse extends ApiResponse {
  data?: { proposals: FactoryProposal[] };
}

export interface FactoryErrorsResponse extends ApiResponse {
  data?: { errors: { at: number; kind: string; message: string; stack: string }[] };
}

export interface FactoryIndexResponse extends ApiResponse {
  data?: { plugins: { id: string; repo: string; author: string; description: string; tags: string[]; kinds: string[] }[] };
}

/** One line of the `POST /api/plugins/{id}/install` ndjson stream. */
export type PluginInstallEvent =
  | { done: false; phase: 'pip' | 'npm'; line: string }
  | { done: true; ok: boolean; error: string | null; status: PluginStatus | null };
