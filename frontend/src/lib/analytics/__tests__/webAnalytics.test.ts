import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';

declare global {
  interface Window {
    __NARRANEXUS_CONFIG__?: Record<string, unknown>;
    dataLayer?: unknown[];
  }
}

// Mutable knobs the mocked deps read via closure — let a test change them
// between two initWebAnalytics() calls against the same module instance.
let optedOut: boolean | 'throw' = false;
let tauri = false;

// Preserve and restore the real jsdom Location via its property descriptor, so
// a test that swaps in a `{ reload }` stub never leaks a broken location to the
// next test (the failure would surface dozens of lines from its cause).
let realLocation: PropertyDescriptor | undefined;
beforeAll(() => {
  realLocation = Object.getOwnPropertyDescriptor(window, 'location');
});
afterAll(() => {
  if (realLocation) Object.defineProperty(window, 'location', realLocation);
});

function gtmScripts(): Element[] {
  return Array.from(
    document.head.querySelectorAll('script[src*="googletagmanager.com/gtm.js"]'),
  );
}

// `started` (and `consentRevoked`) are module-level, so each case must
// re-import a fresh module (vi.resetModules) — otherwise the first successful
// inject makes every later case return early and silently pass.
async function importLoader() {
  vi.doMock('@/lib/tauri', () => ({ isTauri: () => tauri }));
  vi.doMock('@/lib/api', () => ({
    api: {
      getAnalyticsOptOut: () =>
        optedOut === 'throw'
          ? Promise.reject(new Error('lookup failed'))
          : Promise.resolve(optedOut),
    },
  }));
  return import('../webAnalytics');
}

beforeEach(() => {
  vi.resetModules();
  optedOut = false;
  tauri = false;
  window.__NARRANEXUS_CONFIG__ = { gtmId: 'GTM-TEST' };
  document.head.querySelectorAll('script').forEach((s) => s.remove());
});

afterEach(() => {
  delete window.__NARRANEXUS_CONFIG__;
  if (realLocation) Object.defineProperty(window, 'location', realLocation);
  vi.doUnmock('@/lib/tauri');
  vi.doUnmock('@/lib/api');
});

describe('initWebAnalytics', () => {
  test('injects GTM for an opted-in web user', async () => {
    const { initWebAnalytics } = await importLoader();
    await initWebAnalytics();
    expect(gtmScripts().length).toBe(1);
  });

  test('does not inject on desktop (Tauri)', async () => {
    tauri = true;
    const { initWebAnalytics } = await importLoader();
    await initWebAnalytics();
    expect(gtmScripts().length).toBe(0);
  });

  test('does not inject when unconfigured (no id)', async () => {
    delete window.__NARRANEXUS_CONFIG__; // non-official host + no id
    const { initWebAnalytics } = await importLoader();
    await initWebAnalytics();
    expect(gtmScripts().length).toBe(0);
  });

  test('does not inject when the user has opted out', async () => {
    optedOut = true;
    const { initWebAnalytics } = await importLoader();
    await initWebAnalytics();
    expect(gtmScripts().length).toBe(0);
  });

  test('fail-closed: does not inject when the opt-out lookup throws', async () => {
    optedOut = 'throw';
    const { initWebAnalytics } = await importLoader();
    await initWebAnalytics();
    expect(gtmScripts().length).toBe(0);
  });

  test('idempotent: two calls inject only once', async () => {
    const { initWebAnalytics } = await importLoader();
    await initWebAnalytics();
    await initWebAnalytics();
    expect(gtmScripts().length).toBe(1);
  });

  test('reloads to shed GTM when an opted-out user appears after it loaded', async () => {
    const reload = vi.fn();
    Object.defineProperty(window, 'location', { configurable: true, value: { reload } });
    const { initWebAnalytics } = await importLoader();
    optedOut = false;
    await initWebAnalytics(); // user A (opted-in) → GTM loads
    expect(gtmScripts().length).toBe(1);
    optedOut = true;
    await initWebAnalytics(); // user B (opted-out) same tab → reload to shed GTM
    expect(reload).toHaveBeenCalledTimes(1);
  });

  test('consent revoked mid-flight (before the opt-out lookup returns) blocks injection', async () => {
    const { initWebAnalytics, markWebAnalyticsConsentRevoked } = await importLoader();
    markWebAnalyticsConsentRevoked(); // as if the user turned the toggle off
    await initWebAnalytics(); // read opt-out=false, but consent was revoked
    expect(gtmScripts().length).toBe(0);
  });
});

describe('isWebAnalyticsLoaded', () => {
  test('false before load, true after an opted-in load', async () => {
    optedOut = false;
    const { initWebAnalytics, isWebAnalyticsLoaded } = await importLoader();
    expect(isWebAnalyticsLoaded()).toBe(false);
    await initWebAnalytics();
    expect(isWebAnalyticsLoaded()).toBe(true);
  });

  test('stays false when GTM did not load (opted out) — the PrivacySettings reload guard', async () => {
    optedOut = true;
    const { initWebAnalytics, isWebAnalyticsLoaded } = await importLoader();
    await initWebAnalytics();
    expect(isWebAnalyticsLoaded()).toBe(false);
  });
});
