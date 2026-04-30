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

  async getLogs(serviceId?: string): Promise<LogEntry[]> {
    // Wired to the Rust command in tauri/src-tauri/src/commands/health.rs
    // (registered as `get_logs` in lib.rs's invoke_handler).
    // @tauri-apps/api is a desktop-only optional runtime dep, intentionally
    // not declared in package.json. This branch only runs when
    // window.__TAURI__ is present (i.e. inside the Tauri webview); the cloud
    // `tsc -b && vite build` skips type resolution (@ts-expect-error) and
    // static bundling (@vite-ignore).
    type TauriCore = {
      invoke: <T>(cmd: string, args?: Record<string, unknown>) => Promise<T>;
    };
    // Indirect through a variable so Rollup can't statically resolve the
    // module (combined with @vite-ignore to silence Vite's plugin warning).
    const tauriCorePath = '@tauri-apps/api/core';
    const { invoke } = (await import(/* @vite-ignore */ tauriCorePath)) as TauriCore;
    type RustLogEntry = {
      service_id: string;
      timestamp: number;
      stream: string;
      message: string;
    };
    const raw = await invoke<RustLogEntry[]>('get_logs', {
      serviceId: serviceId ?? null,
    });
    return raw.map((e) => ({
      serviceId: e.service_id,
      timestamp: e.timestamp,
      stream: (e.stream === 'stderr' ? 'stderr' : 'stdout') as
        | 'stdout'
        | 'stderr',
      message: e.message,
    }));
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

  async getLogs(serviceId?: string): Promise<LogEntry[]> {
    // In web/cloud mode the operator-facing log endpoints proxy
    // ~/.narranexus/logs/<service>/. If no service is specified we
    // pick the first one returned by /services so the SystemPage at
    // least shows something instead of throwing.
    const base = import.meta.env.VITE_API_BASE_URL || '';
    const headers = await this._authHeaders();

    let target = serviceId;
    if (!target) {
      const listRes = await fetch(`${base}/api/admin/logs/services`, {
        headers,
      });
      if (!listRes.ok) {
        throw new Error(
          `failed to list services: ${listRes.status} ${listRes.statusText}`,
        );
      }
      const listJson: { services: { name: string }[] } = await listRes.json();
      target = listJson.services[0]?.name;
      if (!target) return [];
    }

    const tailRes = await fetch(
      `${base}/api/admin/logs/${encodeURIComponent(target)}/tail?n=500`,
      { headers },
    );
    if (!tailRes.ok) {
      throw new Error(
        `failed to read log: ${tailRes.status} ${tailRes.statusText}`,
      );
    }
    const tailJson: { lines: string[] } = await tailRes.json();
    return tailJson.lines.map((line, idx) => ({
      serviceId: target!,
      // No reliable per-line timestamp without parsing; ordinal index
      // is enough for stable React keys + display ordering.
      timestamp: idx,
      stream: 'stdout',
      message: line,
    }));
  }

  // Inline auth header builder. Tokens are stored alongside other auth
  // state by useAuth; if the page is rendered before login (or in
  // local mode where there's no token) we just send nothing — the
  // server already permits unauthenticated /api/admin/logs in local
  // mode and rejects with 401 in cloud mode.
  private async _authHeaders(): Promise<HeadersInit> {
    const token =
      typeof localStorage !== 'undefined'
        ? localStorage.getItem('auth_token')
        : null;
    return token ? { Authorization: `Bearer ${token}` } : {};
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
