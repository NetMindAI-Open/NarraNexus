import { afterEach, describe, expect, test } from 'vitest';
import { getNetmindConfig } from '../runtimeConfig';

declare global {
  interface Window { __NARRANEXUS_CONFIG__?: Record<string, unknown>; }
}

afterEach(() => { delete window.__NARRANEXUS_CONFIG__; });

describe('getNetmindConfig', () => {
  test('reads injected NetMind keys', () => {
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

  test('falls back to empty strings when nothing injected', () => {
    const c = getNetmindConfig();
    expect(c).toEqual({ authApi: '', accountsUrl: '', sysCode: '', registerUrl: '' });
  });
});
