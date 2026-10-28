/**
 * Team & Bundle types — Subproject 1 + Subproject 2
 */

import type { BusAttachment } from './messages';

export interface Team {
  id?: number | null;
  team_id: string;
  owner_user_id: string;
  name: string;
  description?: string | null;
  color?: string | null;
  source: string;
  intro_md?: string | null;
  // Agent that answers a team-chat message with no @mention (null = earliest member).
  lead_agent_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TeamWithMembers {
  team: Team;
  member_agent_ids: string[];
  /**
   * When this team's room last said something worth coming back for, and what.
   *
   * The sidebar never loads a transcript, so "did anything happen while I was
   * away" cannot be derived on the client — this is the server half of the
   * unread mark, compared against a watermark held per device in localStorage
   * (`lib/unread`). All three are null for a room that does not exist yet or has
   * only the user's own messages and the platform's own notices in it: neither
   * qualifies, or sending a message would mark the room you sent it from.
   */
  last_message_at?: string | null;
  last_message_preview?: string | null;
  last_message_author?: string | null;
}

export interface TeamListResponse {
  teams: TeamWithMembers[];
}

// ----- Team group chat (over the message bus) -----

export interface TeamChatMessage {
  message_id: string;
  from_agent: string;   // 'usr_<user_id>' for the user, else an agent_id
  author_name: string;
  is_user: boolean;
  content: string;
  attachments?: BusAttachment[] | null;
  /** True when this line is the PLATFORM narrating itself rather than a member
   *  speaking. Decided server-side from `PLATFORM_MSG_TYPES`, so the client does
   *  not keep a second copy of a list that grows — one it did not know about
   *  would render with an identity colour, an avatar, and a `team_<id>` marker
   *  for a name. `msg_type` still says WHICH kind, for choosing the wording. */
  is_platform?: boolean;
  /** 'text' | 'multimodal' for ordinary messages. Everything else is the
   *  PLATFORM narrating itself, rendered as a room-level line rather than as
   *  a member speaking: 'system_stop' (owner stopped a run), 'patrol' (the
   *  Leader's sweep), 'system_bulletin' (house rules changed),
   *  'system_undelivered' (a turn ended without delivering anything) and
   *  'system_delivery_failed' (the reply existed; posting it failed).
   *  The last two exist so an empty room can no longer be mistaken for an
   *  agent ignoring you — see message_bus/delivery_notice.py. */
  msg_type?: string | null;
  /** Monologue/reply boundary, when the producing run recorded one. Absent for
   *  every message written before it existed and for any path without a
   *  monologue — the bubble renders those as one block, which is what it did
   *  before. There is no backfill and no guessing (iron rule #2). */
  segments?: Array<{ kind: string; text: string }> | null;
  /** `events` row id of the turn that produced this reply — drives the
   *  per-message reasoning disclosure. Null for user messages / legacy rows. */
  event_id?: string | null;
  created_at: string;
}

/** One phase transition inside a turn: 'starting'|'thinking'|'replying'|'tool:<name>'. */
export interface TeamActivityStep {
  phase: string;
  /** ISO timestamp of the transition. */
  at: string;
}

/** The turn's step timeline, with a count of entries dropped past the cap. */
export interface TeamActivitySteps {
  items: TeamActivityStep[];
  dropped: number;
}

/**
 * One team member's live status in the room.
 *
 * `stalled` is deliberately separate from `queued`: the turn DID start, we
 * just stopped receiving heartbeats from it. Showing both as "queued" is what
 * made a wedged worker indistinguishable from a busy one.
 */
export type TeamMemberStatus = 'running' | 'stalled' | 'queued' | 'idle';

export interface TeamMemberActivity {
  agent_id: string;
  status: TeamMemberStatus;
  /** running / stalled: 'starting'|'thinking'|'replying'|'tool:<name>'. */
  phase?: string | null;
  tool_count?: number;
  /** running / stalled: ISO start time, for elapsed display. */
  started_at?: string | null;
  /** running / stalled: ISO time of the last heartbeat. */
  last_signal_at?: string | null;
  /** idle: ISO time the previous turn ended. */
  finished_at?: string | null;
  /** idle: the previous turn ran and made no `message_team` call. The room
   *  no longer posts a line for that (2026-09-03); the roster shows it. */
  last_turn_silent?: boolean;
  /** queued: how many @mentions are waiting, and since when. */
  queued_count?: number;
  queued_since?: string | null;
  /** running / stalled, and idle when a previous turn's trace survives. */
  steps?: TeamActivitySteps | null;
  /** `events` row id of the member's current/last turn — fetch the full
   *  event_log detail through the existing event-log endpoint. */
  event_id?: string | null;
}

/** One card on the team's work board.
 *
 *  `kind` splits two things the board used to render identically:
 *   - `task`    — an explicit, Leader-maintained item: `title` + one assignee.
 *   - `handoff` — one @message's auto errands, collapsed. It shows
 *                 `source_name → assignee_names` and carries no title, because
 *                 that title was the sender's words, not the assignee's. */
export interface TeamWorkItem {
  item_id: string;
  kind: 'task' | 'handoff';
  title: string;
  assignee_id?: string | null;
  assignee_name?: string | null;
  /** open | in_progress | stalled | done | paused | cancelled.
   *  The user's view only ever receives the first three plus `paused`.
   *  On a hand-off this is the AGGREGATE across its rows, so it can read
   *  `in_progress` while `paused_item_ids` is still non-empty. */
  status: string;
  created_at?: string | null;
  /** Hand-off only: who sent the @message (a teammate, or the user). */
  source_name?: string | null;
  /** Hand-off only: everyone the card still tracks, resolved to names. */
  assignee_names?: string[];
  /** The underlying rows this card stands for — one for a task, several for a
   *  collapsed hand-off, so a paused group resumes row by row. */
  item_ids?: string[];
  /** Which of those rows are currently `paused`. Drives the resume affordance:
   *  a hand-off whose aggregate `status` is `in_progress` can still have parked
   *  rows here (a resume that failed halfway), and resume acts on exactly these. */
  paused_item_ids?: string[];
}

export interface TeamWorkBoardResponse {
  success: boolean;
  items: TeamWorkItem[];
  /** When the lead last swept the board. Null = never patrolled yet. */
  last_patrol_at?: string | null;
  patrol_enabled: boolean;
}

export interface TeamChatHistoryResponse {
  success: boolean;
  channel_id: string;
  messages: TeamChatMessage[];
  /** Per-member live status for the activity console. */
  activity?: TeamMemberActivity[];
  /** Who answers a message with no @mention (lead, else earliest-joined). */
  lead_agent_id?: string | null;
  /** Whether the lead's periodic sweep is on. Rides on the room poll so the
   *  management tab and the work board read one value (2026-09-03). */
  patrol_enabled?: boolean;
}

export interface TeamChatSendResponse {
  success: boolean;
  message_id: string;
  channel_id: string;
}

export interface TeamOperationResponse {
  success: boolean;
  message?: string | null;
  team?: Team | null;
}

// ----- Bundle export -----

export type SkillInstallMethod = 'url' | 'zip' | 'full_copy' | 'builtin' | 'skip';

export interface SkillExportSpec {
  skill_name: string;
  install_method: SkillInstallMethod;
  // Per-agent attribution: same skill name may be installed on N agents
  // and each of them is an independent copy with its own .skill_meta.json
  // (different env_config / study_result). Bundle ships one entry per
  // (agent_id, skill_name) pair so import-side reconstructs each
  // agent's skill state independently.
  agent_id?: string;
  // skill_name comes from SKILL.md frontmatter and CAN duplicate across
  // two physically-different skill dirs under the same agent. skill_dir is
  // the actual filesystem dir name (filesystem-unique within one agent's
  // skills/ dir) — backend uses it to package the right physical folder.
  skill_dir?: string;
  source_url?: string | null;
  source_type?: 'github' | 'zip';
  branch?: string | null;
  // No archive_path / manual_zip_path: for install_method 'zip' the backend
  // resolves the archive from the caller's own skill_archives rows. Echoing a
  // path back from GET /skills/archives used to make it a client-chosen file
  // read (SEC-07). Sending one anyway is *ignored*, not rejected (pydantic
  // defaults to extra="ignore"), which is deliberate: an older DMG build still
  // sends it, and `extra="forbid"` would 422 those clients for a field the
  // server no longer reads.
}

export interface BundleExportRequest {
  agent_ids: string[];
  team_id?: string | null;
  team_intro_md?: string | null;
  skills?: SkillExportSpec[];
  social_entity_selection?: Record<string, string[]>;
  workspace_excludes?: Record<string, string[]>;
  include_chat_history?: boolean;
  // B6: explicit user opt-in to ship zip skill archives that contain sensitive files
  accept_sensitive_zips?: boolean;
  // B2: per-agent narrative allowlist; omit/null = include all
  narrative_selection?: Record<string, string[]> | null;
  // B2: per-narrative event allowlist; omit/null = include all
  event_selection?: Record<string, string[]> | null;
  // P7: per-agent job_id allowlist; omit/null = include all
  job_selection?: Record<string, string[]> | null;
  // Message Bus channel allowlist; omit/null = include all
  // owner-owned channels with ≥1 closure agent member
  bus_channel_selection?: string[] | null;
  // Per-agent MCP allowlist; omit/null/{} = NO MCP shipped (opt-in by design)
  mcp_selection?: Record<string, string[]> | null;
  // Per-agent artifact allowlist; omit/null = include all
  // (workspace files always travel inside workspace.tar.gz regardless)
  artifact_selection?: Record<string, string[]> | null;
  // Opt-in: ship IM channel credentials (Lark/Slack/Telegram/WeChat/Discord/
  // NarraMessenger) so channels can be re-activated in the target env without
  // re-binding. Default false — near-plaintext secrets. Imported creds land
  // inactive; the user activates them in the new environment.
  include_channel_credentials?: boolean;
  // Opt-in: ship skill secrets (.skill_meta.json env_config + full_copy secret
  // files) so migrated skills work without re-auth. Default false — scrubbed on
  // export otherwise. Set together with include_channel_credentials by the
  // "full mode" export checkbox.
  include_skill_secrets?: boolean;
}

// ----- Bundle export previews (wizard helpers) -----

export interface BundleArtifactPreview {
  artifact_id: string;
  title: string;
  kind: string;
  size_bytes: number;
  pinned: boolean;
  session_id?: string | null;
  file_path?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface BundleMcpPreview {
  mcp_id: string;
  name: string;
  url: string;
  description?: string | null;
  is_enabled: boolean;
  connection_status?: string | null;
}

// ----- Bundle import -----

export interface BundlePreflightResponse {
  preflight_token: string;
  manifest: BundleManifest;
  name_clashes: { agent_id_in_bundle: string; agent_name: string; existing_count: number }[];
  team_clash?: { name: string; existing_count: number } | null;
  // Opt-in credential bundles: a bot-identity already bound in this
  // environment. Such credentials are SKIPPED (not overwritten) on confirm.
  credential_clashes?: { agent_id_in_bundle: string; table: string; identity: Record<string, string> }[];
  warnings: string[];
}

export interface BundleManifest {
  bundle_format_version: string;
  narranexus_version_exported: string;
  exported_at: string;
  owner_placeholder: string;
  team?: { team_id: string; name: string; description?: string; color?: string; source: string; intro_md?: string } | null;
  agents: string[];
  agents_summary: any[];
  skills: any[];
  mcp_hints_count?: number;
  // True iff the bundle carries ≥1 IM channel credential (opt-in export).
  contains_channel_credentials?: boolean;
  // True iff the bundle carries skill secrets (opt-in export).
  contains_skill_secrets?: boolean;
  stripped: string[];
  warnings: string[];
  // Non-actionable expected events (e.g. closure-dropped external edges).
  // Server demotes legacy `skipped_external_edge: ...` lines from warnings
  // into here at preflight time, so the UI doesn't alarm on them.
  info?: string[];
  info_counters?: Record<string, number>;
  integrity_sha256: string;
}

export interface BundleConfirmResponse {
  agents_created: number;
  agents_renamed: number;
  team_created: boolean;
  team_id?: string;
  team_name?: string;
  narratives_created: number;
  events_created: number;
  instances_created: number;
  messages_created: number;
  social_entities_created: number;
  rag_rows_created: number;
  skills_imported: number;
  mcp_hints: number;
  mcp_hints_data?: { agent_id: string; name: string; url: string; description?: string }[];
  // Added in bundle format 1.1
  artifacts_created?: number;
  mcp_urls_created?: number;
  // Opt-in IM channel credentials: imported = landed inactive (await manual
  // activation); skipped = a same-bot binding already existed in this env.
  channel_credentials_imported?: number;
  channel_credentials_skipped_conflict?: number;
  warnings: string[];
}

// ----- Skill archive -----

export interface SkillArchiveRecord {
  id?: number;
  user_id: string;
  skill_name: string;
  source_type: 'github' | 'zip';
  source_url?: string | null;
  archive_path?: string | null;
  sha256: string;
  created_at?: string | null;
}

/** One line on a team's bulletin — the standing rules every member loads on
 *  every team turn. `source` drives permissions and rendering; `author_id`
 *  only drives the "added by" label and is null for the auto-summary. */
export interface BulletinEntry {
  entry_id: string;
  team_id: string;
  content: string;
  source: 'user' | 'agent' | 'auto_summary';
  author_id?: string | null;
  tier: 'long_term' | 'current_task';
  created_at?: string | null;
  updated_at?: string | null;
}

/** The bulletin plus the numbers the panel needs to disable "add" BEFORE the
 *  user types, rather than rejecting what they wrote. */
export interface TeamBulletin {
  entries: BulletinEntry[];
  usage: { entry_count: number; total_chars: number };
  limits: { max_entries: number; max_entry_chars: number; max_total_chars: number };
}
