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
  version?: string;
  author?: string;
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
