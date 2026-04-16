/**
 * @file_name: runtimeConfig.ts
 * @date: 2026-04-16
 * @description: Runtime config injected by the deploy pipeline at startup.
 *
 * The deploy repo overwrites /config.js before nginx boots, putting the
 * deployment's intended mode and API URL into `window.__NARRANEXUS_CONFIG__`.
 * index.html loads that script synchronously BEFORE the Vite bundle, so
 * this module can safely read it at any time during app lifecycle.
 *
 * Why runtime instead of build-time:
 *   - One built bundle serves many deployments (dev, staging, per-tenant
 *     EC2). Changing the target URL does NOT require rebuilding the frontend.
 *   - The deploy pipeline is the authority on "is this install cloud or
 *     local?" — not the end user.
 */

export type RuntimeMode = 'cloud' | 'local' | null;

export interface RuntimeConfig {
  /** Forced app mode. `null` = user chooses (dev / Tauri desktop). */
  mode: RuntimeMode;
  /** Base URL for API calls. `""` = same-origin (nginx proxy handles /api/*). */
  apiUrl: string;
}

const DEFAULT_CONFIG: RuntimeConfig = { mode: null, apiUrl: '' };

/**
 * Read the runtime config injected via `/config.js`.
 *
 * Safe to call from any module at any time. Returns a defensive copy —
 * mutations don't leak back into the global.
 */
export function getRuntimeConfig(): RuntimeConfig {
  if (typeof window === 'undefined') return { ...DEFAULT_CONFIG };
  const raw = (window as unknown as { __NARRANEXUS_CONFIG__?: Partial<RuntimeConfig> }).__NARRANEXUS_CONFIG__;
  if (!raw) return { ...DEFAULT_CONFIG };
  const mode: RuntimeMode =
    raw.mode === 'cloud' || raw.mode === 'local' ? raw.mode : null;
  const apiUrl = typeof raw.apiUrl === 'string' ? raw.apiUrl.replace(/\/+$/, '') : '';
  return { mode, apiUrl };
}

/** True if the deploy pipeline has locked the app to cloud mode. */
export function isForcedCloud(): boolean {
  return getRuntimeConfig().mode === 'cloud';
}

/** True if the deploy pipeline has locked the app to local mode. */
export function isForcedLocal(): boolean {
  return getRuntimeConfig().mode === 'local';
}
