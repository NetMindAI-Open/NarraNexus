import { afterAll, afterEach, beforeAll, describe, expect, test, vi } from 'vitest';
import { getWebAnalyticsConfig } from '../runtimeConfig';

declare global {
  interface Window { __NARRANEXUS_CONFIG__?: Record<string, unknown>; }
}

// Preserve the real jsdom Location and restore it exactly, so a test that only
// needed `hostname` never leaves `window.location` as a plain object without
// href / reload for the next test.
let realLocation: PropertyDescriptor | undefined;
beforeAll(() => {
  realLocation = Object.getOwnPropertyDescriptor(window, 'location');
});
afterAll(() => {
  if (realLocation) Object.defineProperty(window, 'location', realLocation);
});

function setHostname(hostname: string) {
  Object.defineProperty(window, 'location', { configurable: true, value: { hostname } });
}

afterEach(() => {
  delete window.__NARRANEXUS_CONFIG__;
  vi.unstubAllEnvs();
  if (realLocation) Object.defineProperty(window, 'location', realLocation);
});

describe('getWebAnalyticsConfig', () => {
  test('injected gtmId wins over everything (host-independent)', () => {
    window.__NARRANEXUS_CONFIG__ = { gtmId: 'GTM-INJECTED' };
    expect(getWebAnalyticsConfig().gtmId).toBe('GTM-INJECTED');
  });

  test('an explicitly injected empty gtmId is a kill-switch, even on the official host', () => {
    setHostname('agent.narra.nexus');
    window.__NARRANEXUS_CONFIG__ = { gtmId: '' };
    expect(getWebAnalyticsConfig().gtmId).toBe('');
  });

  test('VITE_GTM_ID is used when set and nothing is injected', () => {
    setHostname('narranexus.example.com'); // non-official: only VITE can supply it
    vi.stubEnv('VITE_GTM_ID', 'GTM-FROMVITE');
    expect(getWebAnalyticsConfig().gtmId).toBe('GTM-FROMVITE');
  });

  test('compiled-in default is used ONLY on the official production host', () => {
    setHostname('agent.narra.nexus');
    expect(getWebAnalyticsConfig().gtmId).toBe('GTM-W8VXKW7L');
  });

  test('a self-host / dev host gets nothing (no leak to our container)', () => {
    setHostname('narranexus.example.com');
    expect(getWebAnalyticsConfig().gtmId).toBe('');
  });
});
