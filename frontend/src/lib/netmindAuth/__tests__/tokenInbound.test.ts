import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { takeInboundToken } from '../tokenInbound';

afterEach(() => vi.restoreAllMocks());

describe('takeInboundToken', () => {
  test('extracts token + source and strips token from URL', () => {
    const replace = vi.spyOn(window.history, 'replaceState').mockImplementation(() => {});
    const r = takeInboundToken({ search: '?token=abc&source=arena&x=1', pathname: '/app', hash: '' });
    expect(r).toEqual({ handled: true, token: 'abc', source: 'arena' });
    const newUrl = replace.mock.calls[0][2] as string;
    expect(newUrl).not.toContain('token=');
    expect(newUrl).toContain('source=arena');
    expect(newUrl).toContain('x=1');
  });

  test('no token → handled false, source still parsed', () => {
    const r = takeInboundToken({ search: '?source=arena', pathname: '/app', hash: '' });
    expect(r).toEqual({ handled: false, source: 'arena' });
  });
});

describe('captureInboundEntry', () => {
  beforeEach(() => {
    vi.resetModules(); // reset the module-level _inbound cache per test
    sessionStorage.clear();
  });

  // Regression: a logged-out arena entry (`/?source=arena`) must stash `source`
  // into sessionStorage at startup — BEFORE the App tree (and its <Navigate>
  // redirect to /login) can rewrite the URL and drop the param. Capturing it in
  // an App useEffect was too late and silently broke post-login provisioning.
  test('stashes ?source into sessionStorage and caches the entry result', async () => {
    window.history.replaceState(null, '', '/?source=arena');
    const { captureInboundEntry, getInboundEntry, ENTRY_SOURCE_KEY } = await import('../tokenInbound');

    const r = captureInboundEntry();
    expect(r.source).toBe('arena');
    expect(sessionStorage.getItem(ENTRY_SOURCE_KEY)).toBe('arena');

    // Idempotent: a later read returns the same captured result even if the URL
    // has since been rewritten (the redirect race we are guarding against).
    window.history.replaceState(null, '', '/login?next=%2F%3Fsource%3Darena');
    expect(captureInboundEntry()).toEqual(r);
    expect(getInboundEntry()).toEqual(r);
  });

  test('no inbound params → nothing stashed', async () => {
    window.history.replaceState(null, '', '/app/chat');
    const { captureInboundEntry, ENTRY_SOURCE_KEY } = await import('../tokenInbound');

    expect(captureInboundEntry()).toEqual({ handled: false });
    expect(sessionStorage.getItem(ENTRY_SOURCE_KEY)).toBeNull();
  });
});
