/**
 * @file env.d.ts
 * @description Renderer process global type declarations
 */

// ─── Three-Phase Setup Types ───────────────────────────────

type DockerState = 'not_installed' | 'not_running' | 'starting' | 'healthy'

interface PreflightItem {
  id: string
  label: string
  status: 'ok' | 'missing' | 'warning'
  version?: string
  hint?: string
  canAutoInstall: boolean
  manualUrl?: string
  dockerState?: DockerState
}

interface SystemInfo {
  platform: string
  arch: string
  totalMemoryGb: number
  freeDiskGb: number
  networkOk: boolean
}

interface PreflightResult {
  items: PreflightItem[]
  systemInfo: SystemInfo
  allReady: boolean
}

type InstallerStatus = 'pending' | 'running' | 'done' | 'error' | 'skipped'

interface InstallerState {
  id: string
  label: string
  status: InstallerStatus
  currentOutput?: string
  error?: string
  canSkip: boolean
}

type LaunchStepId =
  | 'wait-docker'
  | 'compose-up'
  | 'wait-mysql'
  | 'init-tables'
  | 'wait-evermemos'
  | 'start-services'

type LaunchStepStatus = 'pending' | 'running' | 'done' | 'error' | 'skipped'

interface LaunchStep {
  id: LaunchStepId
  label: string
  status: LaunchStepStatus
  message?: string
}

// ─── Nexus API ───────────────────────────────

/** Nexus API types exposed by Preload */
interface NexusAPI {
  getEnv: () => Promise<{ config: Record<string, string>; fields: EnvField[] }>
  setEnv: (updates: Record<string, string>) => Promise<{ success: boolean }>
  validateEnv: () => Promise<{ valid: boolean; missing: string[]; warnings: string[] }>
  getEverMemOSEnv: () => Promise<{ available: boolean; config: Record<string, string>; fields: EverMemOSEnvField[] }>
  setEverMemOSEnv: (updates: Record<string, string>) => Promise<{ success: boolean }>
  validateEverMemOSEnv: () => Promise<{ valid: boolean; missing: string[]; warnings: string[] }>
  getDockerStatus: () => Promise<{ dockerReady: boolean; groups: DockerGroupStatus[] }>
  startDocker: () => Promise<{ mysql: boolean; evermemos: boolean }>
  stopDocker: () => Promise<{ success: boolean }>
  startAllServices: () => Promise<{ success: boolean }>
  stopAllServices: () => Promise<{ success: boolean }>
  restartService: (serviceId: string) => Promise<{ success: boolean }>
  getServiceStatus: () => Promise<ProcessInfo[]>
  getHealthStatus: () => Promise<OverallHealth>
  initDatabase: () => Promise<{ success: boolean; output: string }>

  // Three-phase setup
  runPreflight: () => Promise<PreflightResult>
  installDep: (id: string) => Promise<{ success: boolean; error?: string }>
  retryDep: (id: string) => Promise<{ success: boolean; error?: string }>
  skipDep: (id: string) => Promise<{ success: boolean }>
  installAllDeps: (missingIds: string[]) => Promise<{ success: boolean; failedId?: string }>
  runLaunch: (options?: { skipEverMemOS?: boolean }) => Promise<{ success: boolean; error?: string }>
  onInstallerUpdate: (callback: (state: InstallerState) => void) => () => void
  onLaunchStep: (callback: (step: LaunchStep) => void) => () => void

  // EverMemOS lifecycle
  launchEverMemOS: () => Promise<{ success: boolean; error?: string }>
  isEverMemOSInstalled: () => Promise<boolean>

  // Claude Code authentication
  getClaudeAuthInfo: () => Promise<ClaudeAuthInfo>
  startClaudeLogin: () => Promise<LoginProcessStatus>
  cancelClaudeLogin: () => Promise<{ success: boolean }>
  sendClaudeLoginInput: (text: string) => Promise<{ success: boolean }>
  saveSetupToken: (token: string) => Promise<{ valid: boolean; message: string }>
  onClaudeLoginStatus: (callback: (status: LoginProcessStatus) => void) => () => void

  // Auto-updater
  checkForUpdates: () => Promise<void>
  downloadUpdate: () => Promise<void>
  installUpdate: () => Promise<void>
  onUpdateStatus: (callback: (status: UpdateStatus) => void) => () => void

  // Miscellaneous
  openExternal: (url: string) => Promise<void>
  getSetupState: () => Promise<{ setupComplete: boolean }>
  setSetupComplete: () => Promise<{ success: boolean }>
  getLogs: (serviceId?: string) => Promise<LogEntry[]>
  onLog: (callback: (entry: LogEntry) => void) => () => void
  onHealthUpdate: (callback: (status: OverallHealth) => void) => () => void
  onTrayAction: (callback: (action: string) => void) => () => void
  onNavigate: (callback: (page: string) => void) => () => void
}

// ─── Other Types ───────────────────────────────

interface EnvField {
  key: string
  label: string
  required: boolean
  placeholder: string
}

interface EverMemOSEnvField {
  key: string
  label: string
  required: boolean
  placeholder: string
  inputType: 'text' | 'password' | 'select'
  options?: string[]
  group: 'llm' | 'vectorize' | 'rerank' | 'infrastructure' | 'other'
  order: number
}

interface DockerGroupStatus {
  id: string
  label: string
  containers: Array<{ name: string; state: string; ports: string }>
  composePath: string
  available: boolean
}

interface ProcessInfo {
  serviceId: string
  label: string
  status: 'stopped' | 'starting' | 'running' | 'crashed'
  pid: number | null
  restartCount: number
  lastError: string | null
}

interface ServiceHealth {
  serviceId: string
  label: string
  state: 'healthy' | 'unhealthy' | 'unknown'
  port: number | null
  lastChecked: number
  message: string
}

interface OverallHealth {
  services: ServiceHealth[]
  infrastructure: ServiceHealth[]
  mysql: 'healthy' | 'unhealthy' | 'unknown'
  allHealthy: boolean
}

interface LogEntry {
  serviceId: string
  timestamp: number
  stream: 'stdout' | 'stderr'
  message: string
}

interface ClaudeAuthStatus {
  state: 'logged_in' | 'expired' | 'not_logged_in' | 'cli_not_installed'
  method?: 'oauth' | 'token'
  expiresAt?: number
  isExpired?: boolean
}

interface ClaudeAuthInfo {
  cliInstalled: boolean
  cliVersion: string | null
  authStatus: ClaudeAuthStatus
  hasApiKey: boolean
  hasSetupToken: boolean
}

interface LoginProcessStatus {
  state: 'idle' | 'running' | 'success' | 'failed' | 'timeout'
  message?: string
}

interface UpdateStatus {
  state: 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error'
  version?: string
  releaseNotes?: string
  progress?: number
  bytesPerSecond?: number
  error?: string
}

interface Window {
  nexus: NexusAPI
}
