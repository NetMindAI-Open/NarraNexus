/**
 * @file_name: platform.ts
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Platform bridge abstraction layer
 *
 * Provides a unified interface for platform-specific operations.
 * Detects runtime (Tauri desktop vs web browser) and returns
 * the appropriate bridge implementation.
 */

import type {
  AppMode,
  AppConfig,
  ProcessInfo,
  OverallHealth,
  LogEntry,
} from '@/types/platform';

export interface PlatformBridge {
  // Service management (local mode only)
  getServiceStatus(): Promise<ProcessInfo[]>;
  startAllServices(): Promise<void>;
  stopAllServices(): Promise<void>;
  restartService(id: string): Promise<void>;
  getLogs(serviceId?: string): Promise<LogEntry[]>;
  onHealthUpdate(cb: (health: OverallHealth) => void): () => void;
  onLog(cb: (entry: LogEntry) => void): () => void;

  // App lifecycle
  getAppMode(): Promise<AppMode>;
  getAppConfig(): Promise<AppConfig>;
  isLocalMode(): boolean;

  // External
  openExternal(url: string): Promise<void>;
}

/**
 * Tauri desktop bridge (placeholder for Phase 4)
 *
 * All methods throw until the Tauri runtime integration is built.
 */
class TauriBridge implements PlatformBridge {
  async getServiceStatus(): Promise<ProcessInfo[]> {
    throw new Error('Tauri runtime not available');
  }

  async startAllServices(): Promise<void> {
    throw new Error('Tauri runtime not available');
  }

  async stopAllServices(): Promise<void> {
    throw new Error('Tauri runtime not available');
  }

  async restartService(_id: string): Promise<void> {
    throw new Error('Tauri runtime not available');
  }

  async getLogs(_serviceId?: string): Promise<LogEntry[]> {
    throw new Error('Tauri runtime not available');
  }

  onHealthUpdate(_cb: (health: OverallHealth) => void): () => void {
    throw new Error('Tauri runtime not available');
  }

  onLog(_cb: (entry: LogEntry) => void): () => void {
    throw new Error('Tauri runtime not available');
  }

  async getAppMode(): Promise<AppMode> {
    throw new Error('Tauri runtime not available');
  }

  async getAppConfig(): Promise<AppConfig> {
    throw new Error('Tauri runtime not available');
  }

  isLocalMode(): boolean {
    return true;
  }

  async openExternal(_url: string): Promise<void> {
    throw new Error('Tauri runtime not available');
  }
}

/**
 * Web browser bridge for cloud deployment
 *
 * Service management is not available in web mode.
 */
class WebBridge implements PlatformBridge {
  async getServiceStatus(): Promise<ProcessInfo[]> {
    throw new Error('Not available in web mode');
  }

  async startAllServices(): Promise<void> {
    throw new Error('Not available in web mode');
  }

  async stopAllServices(): Promise<void> {
    throw new Error('Not available in web mode');
  }

  async restartService(_id: string): Promise<void> {
    throw new Error('Not available in web mode');
  }

  async getLogs(_serviceId?: string): Promise<LogEntry[]> {
    throw new Error('Not available in web mode');
  }

  onHealthUpdate(_cb: (health: OverallHealth) => void): () => void {
    throw new Error('Not available in web mode');
  }

  onLog(_cb: (entry: LogEntry) => void): () => void {
    throw new Error('Not available in web mode');
  }

  async getAppMode(): Promise<AppMode> {
    return 'cloud-web';
  }

  async getAppConfig(): Promise<AppConfig> {
    return {
      mode: 'cloud-web',
      userType: 'external',
      apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '',
    };
  }

  isLocalMode(): boolean {
    return false;
  }

  async openExternal(url: string): Promise<void> {
    window.open(url, '_blank');
  }
}

/**
 * Detect the current platform and return the appropriate bridge.
 * Checks for the Tauri global to decide between desktop and web modes.
 */
function detectPlatform(): PlatformBridge {
  if ((window as any).__TAURI__) {
    return new TauriBridge();
  }
  return new WebBridge();
}

export const platform = detectPlatform();
