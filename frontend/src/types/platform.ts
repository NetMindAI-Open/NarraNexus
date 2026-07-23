/**
 * @file_name: platform.ts
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Platform abstraction types for multi-runtime support.
 *
 * Two surfaces only: `local` (desktop DMG + `bash run.sh` — the sole mode for
 * every local build, no chooser) and `cloud-web` (the hosted website, forced
 * by the deploy pipeline via /config.js). The old `cloud-app` mode (a local
 * build pointing at the cloud backend) was removed — cloud is used via the
 * website.
 */

export type AppMode = 'local' | 'cloud-web';
export type UserType = 'internal' | 'external';

export interface ProcessInfo {
  serviceId: string;
  label: string;
  status: 'stopped' | 'starting' | 'running' | 'crashed';
  pid: number | null;
  restartCount: number;
  lastError: string | null;
}

export type HealthState = 'unknown' | 'healthy' | 'unhealthy';

export interface ServiceHealth {
  serviceId: string;
  label: string;
  state: HealthState;
  port: number | null;
}

export interface OverallHealth {
  services: ServiceHealth[];
  allHealthy: boolean;
}

export interface LogEntry {
  serviceId: string;
  timestamp: number;
  stream: 'stdout' | 'stderr';
  message: string;
}

/**
 * Per-worker liveness inside the consolidated `workers` supervisor process.
 * The four merged workers (poller / jobs / bus / channels) share one OS
 * process, so the process-level ServiceCard cannot show which sub-worker is
 * degraded — this fills that gap. `restartCount` is cumulative, so a climbing
 * count is the "this worker is flapping" signal even while the process is up.
 * Sourced from GET /api/admin/runtime/workers (worker_supervisor heartbeat).
 */
export type WorkerState =
  | 'starting'
  | 'running'
  | 'restarting'
  | 'stopped'
  | 'unknown';

export interface WorkerLiveness {
  name: string;
  state: WorkerState;
  restartCount: number;
  lastError: string | null;
}

export interface WorkerStatus {
  available: boolean;
  heartbeatAgeSeconds: number | null;
  workers: WorkerLiveness[];
}

export interface AppConfig {
  mode: AppMode;
  userType: UserType;
  apiBaseUrl: string;
}

export interface FeatureFlags {
  canUseClaudeCode: boolean;
  canUseApiMode: boolean;
  showSystemPage: boolean;
  showSetupWizard: boolean;
}
