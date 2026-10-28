import { afterEach, describe, expect, test, vi } from 'vitest';
import { getNetmindConfig, isPowerLoginAvailable } from '../runtimeConfig';

declare global {
  interface Window { __NARRANEXUS_CONFIG__?: Record<string, unknown>; }
}

afterEach(() => {
  delete window.__NARRANEXUS_CONFIG__;
  vi.unstubAllEnvs();
});

describe('getNetmindConfig', () => {
  test('injected NetMind keys win over dev defaults', () => {
    window.__NARRANEXUS_CONFIG__ = {
      netmindAuthApi: 'https://userauth.protago-dev.com/',
      netmindAccountsUrl: 'https://accounts.protago-dev.com',
      netmindSysCode: 'f925fc2c',
      netmindRegisterUrl: 'https://example.test/register',
    };
    const c = getNetmindConfig();
    expect(c.authApi).toBe('https://userauth.protago-dev.com'); // trailing slash stripped
    expect(c.accountsUrl).toBe('https://accounts.protago-dev.com');
    expect(c.sysCode).toBe('f925fc2c');
    expect(c.registerUrl).toBe('https://example.test/register');
  });

  test('vite dev server falls back to protago-dev defaults when nothing is set', () => {
    // `npm run dev` without any VITE_NETMIND_* (scripts/dev/netmind_env.sh
    // exports them explicitly for run.sh) still reaches the dev environment.
    vi.stubEnv('DEV', true);
    const c = getNetmindConfig();
    expect(c.authApi).toBe('https://userauth.protago-dev.com');
    expect(c.accountsUrl).toBe('https://accounts.protago-dev.com');
    expect(c.sysCode).toBe('f925fc2c');
  });

  test('a production bundle never falls back to protago-dev', () => {
    // B-40: `vite build` output (desktop DMG, cloud image) with an endpoint
    // left unset must land on PROD NetMind, not the dev OAuth app.
    vi.stubEnv('DEV', false);
    const c = getNetmindConfig();
    expect(c.authApi).toBe('https://auth-api.netmind.ai');
    expect(c.accountsUrl).toBe('https://accounts.netmind.ai');
    expect(c.sysCode).toBe('f925fc2c');
    expect(c.registerUrl).toBe('https://www.netmind.ai/sign/register');
    expect(JSON.stringify(c)).not.toContain('protago-dev');
  });

  test('VITE_* build env beats the compiled-in fallback', () => {
    vi.stubEnv('DEV', true);
    vi.stubEnv('VITE_NETMIND_AUTH_API', 'https://auth-api.netmind.ai/');
    vi.stubEnv('VITE_NETMIND_ACCOUNTS_URL', 'https://accounts.netmind.ai');
    const c = getNetmindConfig();
    expect(c.authApi).toBe('https://auth-api.netmind.ai');
    expect(c.accountsUrl).toBe('https://accounts.netmind.ai');
  });
});

describe('cloud deploy without injected NetMind endpoints', () => {
  // PR#403 review I2: a cloud image is built without VITE_NETMIND_*, so a
  // stack .env that lost NETMIND_AUTH_API_URL / NETMIND_ACCOUNTS_URL would
  // silently fall back to PROD NetMind (wrong for the dev stack). It must
  // log loudly instead. Fresh module per test: the report is once-per-key.
  const fresh = async () => {
    vi.resetModules();
    return import('../runtimeConfig');
  };

  test('logs once per missing key when cloud injects empty values', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    window.__NARRANEXUS_CONFIG__ = {
      mode: 'cloud',
      netmindAuthApi: '',
      netmindAccountsUrl: '',
    };
    const mod = await fresh();
    mod.getNetmindConfig();
    mod.getNetmindConfig();
    expect(err).toHaveBeenCalledTimes(2);
    expect(err.mock.calls.map((c) => String(c[0])).join('\n')).toMatch(
      /NETMIND_AUTH_API_URL[\s\S]*NETMIND_ACCOUNTS_URL/,
    );
    err.mockRestore();
  });

  test('silent when cloud injects both endpoints', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    window.__NARRANEXUS_CONFIG__ = {
      mode: 'cloud',
      netmindAuthApi: 'https://userauth.protago-dev.com',
      netmindAccountsUrl: 'https://accounts.protago-dev.com',
    };
    const mod = await fresh();
    expect(mod.getNetmindConfig().accountsUrl).toBe('https://accounts.protago-dev.com');
    expect(err).not.toHaveBeenCalled();
    err.mockRestore();
  });

  test('silent outside cloud mode (desktop / local rely on VITE_*)', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    window.__NARRANEXUS_CONFIG__ = { mode: 'local' };
    const mod = await fresh();
    mod.getNetmindConfig();
    expect(err).not.toHaveBeenCalled();
    err.mockRestore();
  });
});

describe('isPowerLoginAvailable', () => {
  test('false in a plain local build with no opt-in and no injected endpoints', () => {
    // No forced-cloud, no VITE_ENABLE_POWER_LOGIN, no injected netmindAuthApi.
    expect(isPowerLoginAvailable()).toBe(false);
  });

  test('true when the deploy forces cloud mode', () => {
    window.__NARRANEXUS_CONFIG__ = { mode: 'cloud' };
    expect(isPowerLoginAvailable()).toBe(true);
  });

  test('true when /config.js injects NetMind endpoints (local-mode power deploy)', () => {
    window.__NARRANEXUS_CONFIG__ = {
      mode: 'local',
      netmindAuthApi: 'https://userauth.protago-dev.com',
    };
    expect(isPowerLoginAvailable()).toBe(true);
  });
});
