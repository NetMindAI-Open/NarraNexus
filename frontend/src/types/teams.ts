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
  created_at: string;
}

/** One team member's live activity in the room. */
export interface TeamMemberActivity {
  agent_id: string;
  status: 'running' | 'queued' | 'idle';
  /** running only: 'starting'|'thinking'|'replying'|'tool:<name>'. */
  phase?: string | null;
  tool_count?: number;
  /** running only: ISO start time, for elapsed display. */
  started_at?: string | null;
}

export interface TeamChatHistoryResponse {
  success: boolean;
  channel_id: string;
  messages: TeamChatMessage[];
  /** Member agent_ids the trigger is currently processing → "…" indicator. */
  thinking?: string[];
  /** Per-member activity (running/queued/idle) for the status view. */
  activity?: TeamMemberActivity[];
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
  archive_path?: string | null;
  manual_zip_path?: string | null;
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
