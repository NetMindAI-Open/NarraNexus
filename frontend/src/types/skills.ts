/**
 * @file_name: skills.ts
 * @author: Bin Liang
 * @date: 2026-02-03
 * @description: TypeScript type definitions for Skills module
 */

/**
 * Installation source: github or zip
 */
export type SkillSource = 'github' | 'zip';

/**
 * Skill detail information
 */
export interface SkillInfo {
  name: string;
  description: string;
  path: string;
  disabled: boolean;
  builtin?: boolean;       // Shipped with the app; can be disabled but not removed
  version?: string;
  author?: string;
  source_type?: string;    // marketplace | url | github | zip | builtin | manual
  source_url?: string;     // Installation source URL (saved during GitHub installation)
  installed_at?: string;   // Installation time (ISO format)
  // Environment requirements
  requires_env?: string[];       // Required env var names
  requires_bins?: string[];      // Required binary deps
  env_configured?: boolean;      // Whether all required env vars have values
  // Study status
  study_status?: 'idle' | 'studying' | 'completed' | 'failed';
  study_result?: string;   // Agent study summary
  study_error?: string;    // Study failure error message
  studied_at?: string;     // Study completion time (ISO format)
}

/**
 * Skill list response
 */
export interface SkillListResponse {
  skills: SkillInfo[];
  total: number;
}

/**
 * Skill operation response
 */
export interface SkillOperationResponse {
  success: boolean;
  message?: string;
  skill?: SkillInfo;
}

/**
 * Skill study response
 */
export interface SkillStudyResponse {
  success: boolean;
  message?: string;
  study_status: string;
  study_result?: string;
}

/**
 * Skill env config response
 */
export interface SkillEnvConfigResponse {
  success: boolean;
  requires_env: string[];
  env_configured: Record<string, boolean>;  // var_name -> is_set
}

// ── Skill Marketplace ───────────────────────────────────────────────────────

/** One skill card in marketplace search results (latest published version). */
export interface MarketplaceSkillItem {
  skill_id: string;
  version: string;
  name: string;
  description?: string;
  author?: { name?: string; email?: string } | null;
  category?: string;
  capabilities: string[];
  tags: string[];
  downloads: number;
  avg_rating?: number | null;
  scan_status: 'passed' | 'warning' | 'rejected';
  status: string;
  published_at?: string | null;
  config_schema?: Record<string, unknown> | null;
  dependencies?: Record<string, string>;
  // Injected when the search request carries agent_id
  installed?: boolean;
  update_available?: boolean;
}

export interface MarketplaceSearchResponse {
  items: MarketplaceSkillItem[];
  total: number;
  page: number;
  limit: number;
}

export interface MarketplaceSkillDetail {
  entry: MarketplaceSkillItem;
  versions: { version: string; status: string; published_at?: string | null }[];
  scan: {
    status: string;
    high_issues: number;
    low_issues: number;
    issues: { rule: string; severity: string; file: string; line: number; detail: string }[];
  } | null;
}

export interface MarketplaceInstallResponse {
  status: 'installed';
  skill_id: string;
  version?: string;
  needs_restart: boolean;
  config_required: boolean;
  warnings: { rule: string; severity: string }[];
  replaced_version?: string | null;
}

export interface SkillUpdateInfo {
  skill_id: string;
  installed_version: string;
  latest_version: string;
  description?: string;
}

/**
 * Skill installation request parameters
 */
export interface SkillInstallParams {
  agent_id: string;
  user_id: string;
  source: SkillSource;
  url?: string;
  branch?: string;
  file?: File;
}
