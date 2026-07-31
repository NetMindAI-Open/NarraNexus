/**
 * @file_name: migration.ts
 * @author: NetMind.AI
 * @date: 2026-07-21
 * @description: Frontend types mirroring the Agent Migration standardized JSON
 * contract (src/xyz_agent_context/schema/migration_schema.py) plus the
 * ApplyResult returned by POST /api/migrate/apply.
 *
 * Kept in lock-step with the Python schema — the scanner's `/scan` output is
 * POSTed back verbatim to `/apply`, so the shapes must match. Local/desktop
 * only: `/detect` + `/scan` 503 on cloud.
 */

export type MigrationFramework =
  | 'claude_code'
  | 'hermes'
  | 'openclaw'
  | 'codex'
  | 'custom';

export type MigrationConfidence = 'high' | 'medium' | 'low';

export interface FrameworkDetection {
  framework: MigrationFramework;
  path: string;
  confidence: MigrationConfidence;
  signals: string[];
}

export interface MigrationSource {
  framework: MigrationFramework;
  detected_path: string;
  detection_confidence: MigrationConfidence;
}

export interface MigrationAgent {
  name: string;
  system_prompt: string;
  description: string;
}

export interface MigrationSkill {
  name: string;
  source: string;
  install_hint: string;
  local_path: string | null;
  scope: 'project' | 'global' | '';
}

export interface MigrationTurn {
  role: 'user' | 'assistant';
  text: string;
  ts: string;
}

export interface MigrationSession {
  session_id: string;
  title: string;
  compact_text: string;
  turns: MigrationTurn[];
  started_at: string;
}

export interface MigrationMemory {
  type: string;
  content: string;
  source_file: string;
}

export interface MigrationMcpServer {
  name: string;
  transport: 'stdio' | 'url';
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  headers: Record<string, string>;
  secret_fields: string[];
}

export interface MigrationCustom {
  unmapped_files: string[];
  credential_keys: string[];
  llm_fallback_notes: string;
}

export interface StandardizedAgentImport {
  schema_version: string;
  source: MigrationSource;
  agent: MigrationAgent;
  skills: MigrationSkill[];
  memory: MigrationMemory[];
  mcp_servers: MigrationMcpServer[];
  sessions: MigrationSession[];
  custom: MigrationCustom;
}

export interface MigrationDetectResponse {
  detections: FrameworkDetection[];
}

/** POST /api/migrate/apply result — per-dimension counts of what landed. */
export interface MigrationApplyResult {
  agent_id: string;
  created: boolean;
  awareness_written: boolean;
  memory_written: number;
  default_skills_installed: string[];
  skills_copied: string[];
  skills_installed: string[];
  skills_unmatched: string[];
  mcp_added: string[];
  mcp_stdio_skipped: string[];
  narratives_created: string[];
  memory_turns_retained: number;
  warnings: string[];
}
