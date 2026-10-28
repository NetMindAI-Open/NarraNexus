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

// Compiled-in fallbacks for NetMind endpoints, used per field only when
// neither an injected /config.js nor a VITE_* build env supplies it.
// Precedence per field:
//   injected /config.js  →  VITE_* build env  →  fallback below.
//
// Which fallback depends on the kind of build, decided by vite itself:
//   - `vite` dev server (import.meta.env.DEV): the protago-dev environment,
//     so a developer's bare `npm run dev` reaches the dev NetMind stack.
//   - `vite build` (DMG, cloud image): PROD NetMind. A shipped bundle must
//     never silently fall back to the dev OAuth app — that is how a local
//     "Sign in with GitHub" ended up on "Netmind AI Test" at
//     accounts.protago-dev.com (B-40). The dev literals are dead code in a
//     production build and are stripped from the bundle, which
//     scripts/release/check_desktop_netmind_env.sh asserts for the DMG.
// run.sh / dev-local.sh export explicit VITE_NETMIND_* (scripts/dev/
// netmind_env.sh, PROD by default), so neither fallback applies there.
const _DEV_NETMIND: NetmindConfig = {
  authApi: 'https://userauth.protago-dev.com',
  accountsUrl: 'https://accounts.protago-dev.com',
  sysCode: 'f925fc2c',
  registerUrl: 'https://www.netmind.ai/sign/register',
};

const _PROD_NETMIND: NetmindConfig = {
  authApi: 'https://auth-api.netmind.ai',
  accountsUrl: 'https://accounts.netmind.ai',
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

// The two endpoints a cloud stack MUST inject through /config.js. A cloud
// image is built without any VITE_NETMIND_*, so an empty injected value
// silently lands on the compiled-in fallback — PROD NetMind in a `vite
// build`. That is right for the prod stack but breaks login on the dev
// stack, whose backend validates tokens against protago-dev (PR#403 review
// I2). The fallback cannot know which stack it is on, so a cloud deploy
// that forgot to inject them says so loudly, once per missing key.
const _CLOUD_REQUIRED_NETMIND: ReadonlyArray<[keyof NetmindConfig, string]> = [
  ['authApi', 'NETMIND_AUTH_API_URL'],
  ['accountsUrl', 'NETMIND_ACCOUNTS_URL'],
];
const _reportedUninjected = new Set<string>();

function _reportUninjectedCloudNetmind(injected: Partial<NetmindConfig>): void {
  if (!isForcedCloud()) return;
  for (const [key, envName] of _CLOUD_REQUIRED_NETMIND) {
    if (injected[key] || _reportedUninjected.has(key)) continue;
    _reportedUninjected.add(key);
    console.error(
      `[runtimeConfig] cloud deploy did not inject NetMind "${key}" via ` +
        `/config.js; using the compiled-in fallback. Set ${envName} in the ` +
        'stack .env so the frontend and backend target the same NetMind.',
    );
  }
}

/**
 * NetMind endpoint config. Resolves each field with precedence
 * injected /config.js → VITE_* → compiled-in fallback (protago-dev on the
 * vite dev server, PROD NetMind in any `vite build` output), so a single
 * built bundle serves cloud (real values injected), desktop (VITE_* baked
 * in), and dev (`npm run dev`, dev defaults).
 */
export function getNetmindConfig(): NetmindConfig {
  const injected = _injectedNetmind();
  const vite = _viteNetmind();
  const fallback = import.meta.env.DEV ? _DEV_NETMIND : _PROD_NETMIND;
  _reportUninjectedCloudNetmind(injected);
  const pick = (k: keyof NetmindConfig): string =>
    injected[k] || vite[k] || fallback[k];
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
 * NOT keyed on the compiled-in fallbacks alone — those provide endpoint
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

// Hosts on which the compiled-in analytics id is allowed to load. Gated on the
// real hostname, NOT isForcedCloud() — the deploy stack defaults
// NARRANEXUS_FORCE_MODE=cloud, so mode-based gating would be true for every
// self-host and leak their users' data into our container.
const _OFFICIAL_ANALYTICS_HOSTS = new Set(['agent.narra.nexus']);
// Public client-side GTM container id (it ships in the served HTML on the cloud
// site), not a secret. See getWebAnalyticsConfig for when it is used.
const _CLOUD_GTM_ID = 'GTM-W8VXKW7L';

function _isOfficialAnalyticsHost(): boolean {
  return (
    typeof window !== 'undefined' &&
    _OFFICIAL_ANALYTICS_HOSTS.has(window.location.hostname)
  );
}

export interface WebAnalyticsConfig {
  /** Google Tag Manager container id. "" disables GTM. */
  gtmId: string;
}

/**
 * Google Tag Manager container id. Precedence:
 *   1. injected /config.js `gtmId` key present (even "" = explicit kill-switch)
 *   2. VITE_GTM_ID build env (non-empty)
 *   3. compiled-in default, but ONLY on an official production host
 *      (agent.narra.nexus)
 *   4. "" (disabled) — dev, CI, desktop, and every self-hosted deployment
 *
 * Steps 1-2 are latent capabilities: NO deploy injects `gtmId` and no build
 * sets VITE_GTM_ID today, so production's only live source is step 3 (host +
 * this constant). The immediate off-switch is the GTM console, not a redeploy.
 *
 * NOTE — empty-string semantics differ from getNetmindConfig()/_str, where ""
 * means "unset, fall through to default". Here an explicitly injected "" MUST
 * mean "force off" (a deploy kill-switch), so we branch on `key in injected`,
 * not on truthiness. Do not unify the two parsers without preserving this.
 *
 * Consumed only by lib/analytics/webAnalytics.ts, which additionally gates on
 * isTauri() and the user's product-analytics opt-out.
 */
export function getWebAnalyticsConfig(): WebAnalyticsConfig {
  const injected =
    typeof window !== 'undefined'
      ? (window as unknown as { __NARRANEXUS_CONFIG__?: { gtmId?: unknown } })
          .__NARRANEXUS_CONFIG__
      : undefined;
  if (injected && 'gtmId' in injected) {
    const v = injected.gtmId;
    return { gtmId: typeof v === 'string' ? v.trim() : '' };
  }
  const envVal = (import.meta.env as Record<string, string | undefined>).VITE_GTM_ID;
  if (typeof envVal === 'string' && envVal.trim()) return { gtmId: envVal.trim() };
  if (_isOfficialAnalyticsHost()) return { gtmId: _CLOUD_GTM_ID };
  return { gtmId: '' };
}
