/**
 * @file setup-types.ts
 * @description Three-phase setup shared type definitions
 *
 * Phase 1: Preflight — parallel dependency detection
 * Phase 2: Guided Install — individual installer per dependency
 * Phase 3: Service Launch — start Docker containers + backend services
 */

// ─── Phase 1: Preflight ───────────────────────────────────────

/** Docker daemon state (fine-grained, solves the 500 error misdiagnosis bug) */
export type DockerState = 'not_installed' | 'not_running' | 'starting' | 'healthy'

export interface PreflightItem {
  id: string
  label: string
  status: 'ok' | 'missing' | 'warning'
  version?: string
  hint?: string
  canAutoInstall: boolean
  manualUrl?: string
  /** Docker-specific: fine-grained daemon state */
  dockerState?: DockerState
}

export interface SystemInfo {
  platform: string
  arch: string
  totalMemoryGb: number
  freeDiskGb: number
  networkOk: boolean
}

export interface PreflightResult {
  items: PreflightItem[]
  systemInfo: SystemInfo
  /** All required dependencies are ready — can skip Phase 2 */
  allReady: boolean
}

// ─── Phase 2: Guided Install ───────────────────────────────────

export type InstallerStatus = 'pending' | 'running' | 'done' | 'error' | 'skipped'

export interface InstallerState {
  id: string
  label: string
  status: InstallerStatus
  currentOutput?: string
  error?: string
  canSkip: boolean
}

// ─── Phase 3: Service Launch ───────────────────────────────────

export type LaunchStepId =
  | 'wait-docker'
  | 'compose-up'
  | 'wait-mysql'
  | 'wait-synapse'
  | 'init-tables'
  | 'wait-evermemos'
  | 'start-services'

export type LaunchStepStatus = 'pending' | 'running' | 'done' | 'error' | 'skipped'

export interface LaunchStep {
  id: LaunchStepId
  label: string
  status: LaunchStepStatus
  message?: string
}
