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

export interface NetmindConfig {
  /** NetMind auth API base (server: auth-api.netmind.ai / dev: userauth.protago-dev.com). */
  authApi: string;
  /** NetMind accounts domain hosting the OAuth auth.html popup. */
  accountsUrl: string;
  /** Multi-tenant login code; shared with Power so tokens are interchangeable. */
  sysCode: string;
  /** External NetMind registration page URL for the Sign-up link. */
  registerUrl: string;
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

const _str = (v: unknown): string =>
  typeof v === 'string' ? v.replace(/\/+$/, '') : '';

// Compiled-in DEV defaults for NetMind endpoints. Desktop/Tauri and plain
// `npm run dev` builds have no injected /config.js, so without a fallback they
// could never offer Power login. These point at the protago-dev environment
// (the same one dev-agent.narra.nexus uses). Precedence per field:
//   injected /config.js  →  VITE_* build env  →  these dev defaults.
// A forced-cloud PROD deploy's real /config.js values therefore always win.
const _DEV_NETMIND: NetmindConfig = {
  authApi: 'https://userauth.protago-dev.com',
  accountsUrl: 'https://accounts.protago-dev.com',
  sysCode: 'f925fc2c',
  registerUrl: 'https://www.netmind.ai/sign/register',
};

/** Raw NetMind endpoint values injected via /config.js (no dev fallback). */
function _injectedNetmind(): Partial<NetmindConfig> {
  if (typeof window === 'undefined') return {};
  const raw = (window as unknown as {
    __NARRANEXUS_CONFIG__?: Record<string, unknown>;
  }).__NARRANEXUS_CONFIG__ || {};
  return {
    authApi: _str(raw.netmindAuthApi),
    accountsUrl: _str(raw.netmindAccountsUrl),
    sysCode: typeof raw.netmindSysCode === 'string' ? raw.netmindSysCode : '',
    registerUrl: _str(raw.netmindRegisterUrl),
  };
}

/** NetMind endpoint values from build-time VITE_* env (desktop / dev builds). */
function _viteNetmind(): Partial<NetmindConfig> {
  const e = import.meta.env as Record<string, string | undefined>;
  return {
    authApi: _str(e.VITE_NETMIND_AUTH_API),
    accountsUrl: _str(e.VITE_NETMIND_ACCOUNTS_URL),
    sysCode: typeof e.VITE_NETMIND_SYS_CODE === 'string' ? e.VITE_NETMIND_SYS_CODE : '',
    registerUrl: _str(e.VITE_NETMIND_REGISTER_URL),
  };
}

/**
 * NetMind endpoint config. Resolves each field with precedence
 * injected /config.js → VITE_* → compiled-in dev default, so a single built
 * bundle serves cloud (real values injected), desktop (VITE_* baked in), and
 * dev (`npm run dev`, dev defaults).
 */
export function getNetmindConfig(): NetmindConfig {
  const injected = _injectedNetmind();
  const vite = _viteNetmind();
  const pick = (k: keyof NetmindConfig): string =>
    injected[k] || vite[k] || _DEV_NETMIND[k];
  return {
    authApi: pick('authApi'),
    accountsUrl: pick('accountsUrl'),
    sysCode: pick('sysCode'),
    registerUrl: pick('registerUrl'),
  };
}

const _TRUTHY = new Set(['1', 'true', 'yes']);

/**
 * May the user sign in with a NetMind ("Power") account on this install?
 *
 * Deployment-level capability (the frontend twin of the backend's
 * `is_power_login_enabled()`). True when:
 *   - the deploy is forced-cloud (NetMind login is the only login), OR
 *   - the build opted in via VITE_ENABLE_POWER_LOGIN (desktop / local dual-mode
 *     builds — kept in lockstep with the backend NARRANEXUS_ENABLE_POWER_LOGIN
 *     env so we never show a Power entry the backend would 404), OR
 *   - a /config.js explicitly injected NetMind endpoints (a local-mode deploy
 *     that wired Power login without the build flag).
 *
 * NOT keyed on the compiled-in dev defaults alone — those provide endpoint
 * VALUES once Power login is enabled, not the availability decision.
 */
export function isPowerLoginAvailable(): boolean {
  if (isForcedCloud()) return true;
  const flag = String(
    (import.meta.env as Record<string, string | undefined>).VITE_ENABLE_POWER_LOGIN ?? '',
  ).trim().toLowerCase();
  if (_TRUTHY.has(flag)) return true;
  return !!_injectedNetmind().authApi;
}
