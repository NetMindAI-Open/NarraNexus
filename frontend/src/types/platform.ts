/**
 * @file_name: platform.ts
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Platform abstraction types for multi-runtime support (local / cloud-app / cloud-web)
 */

export type AppMode = 'local' | 'cloud-app' | 'cloud-web';
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
