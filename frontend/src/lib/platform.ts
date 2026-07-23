/**
 * @file_name: platform.ts
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Platform bridge abstraction layer
 *
 * Provides a unified interface for platform-specific operations.
 * Detects runtime (Tauri desktop vs web browser) and returns
 * the appropriate bridge implementation.
 *
 * 2026-05-28 — `TauriBridge` was a half-built Phase-4 placeholder
 * (every method except `getLogs` threw "Tauri runtime not available").
 * The Rust side already exposes get_service_status, get_health_status,
 * start_all_services, stop_all_services, restart_service, get_logs in
 * `lib.rs::invoke_handler`, so wiring them up is a one-to-one mapping
 * via `invoke()`. The `onHealthUpdate` / `onLog` event-subscription
 * methods stay as no-ops because Rust does not emit these events;
 * `SystemPage` was updated to poll `getHealthStatus()` + `getLogs()`
 * on its existing 3 s interval instead.
 */

import type {
  AppMode,
  AppConfig,
  ProcessInfo,
  OverallHealth,
  LogEntry,
} from '@/types/platform';
import { isTauri, invokeTauri } from '@/lib/tauri';

export interface PlatformBridge {
  // Service management (local mode only)
  getServiceStatus(): Promise<ProcessInfo[]>;
  getHealthStatus(): Promise<OverallHealth>;
  startAllServices(): Promise<void>;
  stopAllServices(): Promise<void>;
  restartService(id: string): Promise<void>;
  getLogs(serviceId?: string): Promise<LogEntry[]>;

  // App lifecycle
  getAppMode(): Promise<AppMode>;
  getAppConfig(): Promise<AppConfig>;
  isLocalMode(): boolean;

  // External
  openExternal(url: string): Promise<void>;
}

/**
 * Invoke a Tauri command. Delegates to `lib/tauri.ts`'s `invokeTauri`, which
 * goes through `window.__TAURI_INTERNALS__.invoke` — NO `@tauri-apps/api` npm
 * dependency.
 *
 * The previous implementation did `await import('@tauri-apps/api/core')`, but
 * that package is not installed, so the bundler emitted a bare
 * `import("@tauri-apps/api/core")` specifier the webview cannot resolve → every
 * TauriBridge call threw at runtime. It was never caught because detection
 * always fell through to WebBridge (the withGlobalTauri detection bug), so this
 * path only started running once that was fixed.
 */
async function tauriInvoke<T>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T> {
  return invokeTauri<T>(cmd, args);
}

/**
 * Tauri desktop bridge — fully wired to the Rust `invoke_handler` commands
 * in `tauri/src-tauri/src/commands/`.
 */
class TauriBridge implements PlatformBridge {
  async getServiceStatus(): Promise<ProcessInfo[]> {
    // Rust returns `Vec<ProcessInfo>` serialised with
    // `#[serde(rename_all = "camelCase")]`, so the JSON shape already
    // matches the TS `ProcessInfo` interface exactly.
    return await tauriInvoke<ProcessInfo[]>('get_service_status');
  }

  async getHealthStatus(): Promise<OverallHealth> {
    // Same camelCase mapping — JSON shape matches `OverallHealth` directly.
    return await tauriInvoke<OverallHealth>('get_health_status');
  }

  async startAllServices(): Promise<void> {
    await tauriInvoke<void>('start_all_services');
  }

  async stopAllServices(): Promise<void> {
    await tauriInvoke<void>('stop_all_services');
  }

  async restartService(id: string): Promise<void> {
    // Tauri auto-converts the camelCase argument key to snake_case to
    // match the Rust `service_id: String` parameter name.
    await tauriInvoke<void>('restart_service', { serviceId: id });
  }

  async getLogs(serviceId?: string): Promise<LogEntry[]> {
    // camelCase JSON keys (per the serde directive on Rust LogEntry) —
    // pre-2026-05-28 code did `e.service_id` which silently produced
    // undefined serviceId values in the log viewer.
    return await tauriInvoke<LogEntry[]>('get_logs', {
      serviceId: serviceId ?? null,
    });
  }

  async getAppMode(): Promise<AppMode> {
    return await tauriInvoke<AppMode>('get_app_mode');
  }

  async getAppConfig(): Promise<AppConfig> {
    return await tauriInvoke<AppConfig>('get_app_config');
  }

  isLocalMode(): boolean {
    return true;
  }

  async openExternal(url: string): Promise<void> {
    // Routes through tauri-plugin-shell — same path the global
    // `<a target="_blank">` interceptor uses (see lib/tauri.ts::openExternal).
    await tauriInvoke<void>('plugin:shell|open', { path: url });
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

  async getHealthStatus(): Promise<OverallHealth> {
    throw new Error('Not available in web mode');
  }

  async startAllServices(): Promise<void> {
    throw new Error('Not available in web mode');
  }

  async stopAllServices(): Promise<void> {
    throw new Error('Not available in web mode');
  }

  async restartService(): Promise<void> {
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
 *
 * Uses the shared `isTauri()` (lib/tauri.ts), which checks BOTH
 * `window.__TAURI_INTERNALS__` and `window.__TAURI__`. This matters on Tauri
 * v2: `__TAURI__` is only injected when `app.withGlobalTauri` is true (it is
 * NOT set here), so the old `if (window.__TAURI__)` check always fell through
 * to WebBridge *inside the packaged desktop app* — the System page then showed
 * "Not available in web mode" on the DMG. `__TAURI_INTERNALS__` is always
 * present in v2 (it is what `@tauri-apps/api`'s `invoke` uses), so `isTauri()`
 * detects the desktop webview correctly regardless of `withGlobalTauri`.
 */
export function detectPlatform(): PlatformBridge {
  return isTauri() ? new TauriBridge() : new WebBridge();
}

export const platform = detectPlatform();
