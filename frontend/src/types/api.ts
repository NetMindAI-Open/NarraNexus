/**
 * API response type definitions
 */

/** Base interface for all API responses */
export interface ApiResponse {
  success: boolean;
  error?: string;
}

// Job types
export type JobStatus = 'pending' | 'active' | 'running' | 'paused' | 'paused_no_quota' | 'cooling' | 'blocked' | 'blocked_failed' | 'completed' | 'failed' | 'cancelled';
export type JobType = 'one_off' | 'scheduled' | 'ongoing';

export interface TriggerConfig {
  trigger_type?: string;
  interval_seconds?: number;
  cron_expression?: string;
  timezone?: string;
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

// Awareness types
export interface AwarenessResponse extends ApiResponse {
  awareness?: string;
  create_time?: string;
  update_time?: string;
}

// Clear history types
export interface ClearHistoryResponse extends ApiResponse {
  narrative_ids_deleted: string[];
  narratives_count: number;
  events_count: number;
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
}

export interface EventLogResponse extends ApiResponse {
  event_id: string;
  thinking?: string;
  tool_calls: EventLogToolCall[];
  // Ordered, time-preserving view; the UI prefers this when present and
  // falls back to (thinking, tool_calls) only for old backends that
  // haven't been redeployed yet.
  timeline?: EventLogTimelineEntry[];
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

export interface AgentInfo {
  agent_id: string;
  name?: string;
  description?: string;
  status?: string;
  created_at?: string;
  is_public?: boolean;
  created_by?: string;
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
  display_name?: string;
  email?: string;
  has_system_quota?: boolean;
  initial_input_tokens?: number;
  initial_output_tokens?: number;
}

// Response from /api/auth/register. Carries the optional system free-tier
// quota fields so the client can render a welcome toast without a follow-up
// API call. has_system_quota is false in local mode or when the feature is
// disabled server-side.
export interface RegisterResponse extends ApiResponse {
  user_id?: string;
  token?: string;
  has_system_quota?: boolean;
  initial_input_tokens?: number;
  initial_output_tokens?: number;
}

// Response shape for GET /api/quota/me. Discriminated by `enabled` and
// `status` so the UI can switch exhaustively without "is the feature on"
// booleans scattered through the component tree.
export type QuotaMeResponse =
  | { enabled: false }
  | { enabled: true; status: 'uninitialized' }
  | {
      enabled: true;
      status: 'active' | 'exhausted' | 'disabled';
      remaining_input_tokens: number;
      remaining_output_tokens: number;
      initial_input_tokens: number;
      initial_output_tokens: number;
      granted_input_tokens: number;
      granted_output_tokens: number;
      used_input_tokens: number;
      used_output_tokens: number;
      // User's choice: when true, route LLM calls through the system-default
      // provider even when they have their own provider configured.
      prefer_system_override: boolean;
    };

export interface CreateUserResponse extends ApiResponse {
  user_id?: string;
}

export interface AgentListResponse extends ApiResponse {
  agents: AgentInfo[];
  count: number;
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
  description?: string;
  is_enabled?: boolean;
}

export interface MCPUpdateRequest {
  name?: string;
  url?: string;
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
export interface CostModelBreakdown {
  cost: number;
  input_tokens: number;
  output_tokens: number;
  call_count: number;
}

export interface CostDailyEntry {
  date: string;
  input_tokens: number;
  output_tokens: number;
}

export interface CostSummary {
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
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
  | 'MATRIX'
  | 'LARK';

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

export type JobQueueStatus = 'pending' | 'active' | 'blocked' | 'paused' | 'failed' | 'cooling' | 'paused_no_quota' | 'blocked_failed';

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
export interface LarkCredentialData {
  agent_id: string;
  app_id: string;
  brand: string;
  bot_name: string;
  owner_open_id: string;
  owner_name: string;
  auth_status: string;
  is_active: boolean;
}

export interface LarkCredentialResponse extends ApiResponse {
  data: LarkCredentialData | null;
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
  created_at: string | null;
  updated_at: string | null;
}

export interface SlackCredentialResponse extends ApiResponse {
  data: SlackCredentialData | null;
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
  created_at: string | null;
  updated_at: string | null;
}

export interface TelegramCredentialResponse extends ApiResponse {
  data: TelegramCredentialData | null;
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
  created_at: string | null;
  updated_at: string | null;
}

export interface NarramessengerCredentialResponse extends ApiResponse {
  data: NarramessengerCredentialData | null;
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
  created_at: string | null;
  updated_at: string | null;
}

export interface WeChatCredentialResponse extends ApiResponse {
  data: WeChatCredentialData | null;
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
  created_at: string | null;
  updated_at: string | null;
}

export interface DiscordCredentialResponse extends ApiResponse {
  data: DiscordCredentialData | null;
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
