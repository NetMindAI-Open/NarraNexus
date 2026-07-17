import { afterEach, describe, expect, test } from 'vitest';
import { getNetmindConfig, isPowerLoginAvailable } from '../runtimeConfig';

declare global {
  interface Window { __NARRANEXUS_CONFIG__?: Record<string, unknown>; }
}

afterEach(() => { delete window.__NARRANEXUS_CONFIG__; });

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

  test('falls back to compiled-in dev defaults when nothing injected', () => {
    // Desktop / `npm run dev` builds have no /config.js; dev defaults let them
    // still offer Power login (pointed at protago-dev).
    const c = getNetmindConfig();
    expect(c.authApi).toBe('https://userauth.protago-dev.com');
    expect(c.accountsUrl).toBe('https://accounts.protago-dev.com');
    expect(c.sysCode).toBe('f925fc2c');
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
