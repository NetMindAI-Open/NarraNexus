/**
 * @file_name: sessionGuard.test.ts
 * @description: A 401 must destroy the session ONLY when it is really the
 * session that died.
 *
 * The 2026-08-02 demo incident: `lib/api.ts` treated any 401 carrying an
 * Authorization header as "your JWT is dead" and logged the user out — the
 * whole protected route tree unmounted, the WS dropped, in-flight state
 * vanished. Users described it as "the page reloaded and everything got
 * confusing". Backend logs that hour showed 401s on `/api/providers` and
 * `/api/notices`, endpoints whose 401 can mean "your NetMind token is
 * stale" or "this handler failed to resolve an identity" — neither of
 * which says anything about the session JWT.
 *
 * Two defences are pinned here:
 *   1. classification — only SESSION_DEAD codes are session death;
 *   2. confirmation — even a session-death code is verified against
 *      GET /api/auth/session before anything is torn down.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { isSessionDeadFailure, readAuthCode } from '../authFailure';
import { confirmSessionDeath, resetSessionGuard } from '../sessionGuard';

describe('readAuthCode / isSessionDeadFailure', () => {
  test('session-death codes are recognised', () => {
    for (const code of ['token_expired', 'token_invalid', 'token_missing', 'identity_missing']) {
      expect(isSessionDeadFailure({ detail: 'x', code })).toBe(true);
    }
  });

  test('a stale NetMind token is NOT session death', () => {
    // The 8/2 `/api/providers` 401. The NarraNexus session was fine.
    expect(
      isSessionDeadFailure({ detail: 'NetMind token invalid or expired', code: 'netmind_token_invalid' }),
    ).toBe(false);
  });

  test('a handler that could not resolve an identity is NOT session death', () => {
    expect(isSessionDeadFailure({ detail: 'Authentication required', code: 'identity_unresolved' })).toBe(false);
  });

  test('an unknown code is NOT session death', () => {
    // Safe by default: a code this frontend has never heard of must not be
    // able to log anyone out. New backend codes are opt-in, not opt-out.
    expect(isSessionDeadFailure({ detail: 'x', code: 'some_future_code' })).toBe(false);
  });

  test('a 401 with no code at all is NOT session death', () => {
    expect(isSessionDeadFailure({ detail: 'Authentication required' })).toBe(false);
    expect(isSessionDeadFailure(null)).toBe(false);
    expect(isSessionDeadFailure('not json')).toBe(false);
  });

  test('readAuthCode extracts the code and tolerates junk', () => {
    expect(readAuthCode({ code: 'token_expired' })).toBe('token_expired');
    expect(readAuthCode({})).toBeNull();
    expect(readAuthCode(undefined)).toBeNull();
    expect(readAuthCode({ code: 42 })).toBeNull();
  });
});

describe('confirmSessionDeath', () => {
  let dispatched: Event[];
  let listener: (e: Event) => void;

  beforeEach(() => {
    resetSessionGuard();
    dispatched = [];
    listener = (e: Event) => dispatched.push(e);
    window.addEventListener('narranexus:auth-expired', listener);
    localStorage.setItem(
      'narra-nexus-config',
      JSON.stringify({ state: { token: 'jwt', userId: 'alice' } }),
    );
  });

  afterEach(() => {
    window.removeEventListener('narranexus:auth-expired', listener);
    vi.restoreAllMocks();
    localStorage.clear();
  });

  test('probe agrees the session is dead → auth-expired fires', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401 })));
    await confirmSessionDeath({ endpoint: '/api/agents', code: 'token_expired' });
    expect(dispatched).toHaveLength(1);
  });

  test('probe says the session is alive → nothing is torn down', async () => {
    // The whole point: one endpoint 401'd, but the session is fine. This is
    // the path that used to bounce a working user to /login.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ user_id: 'alice', expires_at: 1 }), { status: 200 }),
      ),
    );
    await confirmSessionDeath({ endpoint: '/api/teams/t1/chat/messages', code: 'token_expired' });
    expect(dispatched).toHaveLength(0);
  });

  test('probe unreachable → nothing is torn down', async () => {
    // Offline / backend restarting. Killing the session here would be the
    // same nuclear failure mode, just triggered by a flaky network.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')));
    await confirmSessionDeath({ endpoint: '/api/agents', code: 'token_expired' });
    expect(dispatched).toHaveLength(0);
  });

  test('probe 500 → nothing is torn down', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 500 })));
    await confirmSessionDeath({ endpoint: '/api/agents', code: 'token_expired' });
    expect(dispatched).toHaveLength(0);
  });

  test('a burst of 401s produces ONE probe and ONE logout', async () => {
    // A page mount fires many requests at once; every one of them 401s when
    // the JWT is dead. Un-deduped, that was N logouts and N banners.
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 401 }));
    vi.stubGlobal('fetch', fetchMock);
    await Promise.all([
      confirmSessionDeath({ endpoint: '/api/agents', code: 'token_expired' }),
      confirmSessionDeath({ endpoint: '/api/notices', code: 'token_expired' }),
      confirmSessionDeath({ endpoint: '/api/quota/me', code: 'token_expired' }),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(dispatched).toHaveLength(1);
  });

  test('the triggering endpoint and code ride along on the event', async () => {
    // So the next incident is diagnosable from the frontend side too.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401 })));
    await confirmSessionDeath({ endpoint: '/api/providers', code: 'token_invalid' });
    const detail = (dispatched[0] as CustomEvent).detail;
    expect(detail).toMatchObject({ endpoint: '/api/providers', code: 'token_invalid' });
  });

  test('no token attached → no probe, no logout', async () => {
    localStorage.clear();
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await confirmSessionDeath({ endpoint: '/api/agents', code: 'token_expired' });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(dispatched).toHaveLength(0);
  });
});
