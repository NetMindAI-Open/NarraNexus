/**
 * @file_name: api.authFailure.test.ts
 * @description: End-to-end wiring of the 401 path through `ApiClient.request`.
 *
 * The unit tests in sessionGuard.test.ts pin the decision logic; this file
 * pins that api.ts actually routes 401s through it. The 2026-08-02 bug was
 * exactly a wiring bug — the logic ("billing 401s are not session death")
 * existed, but as a hardcoded endpoint list that `/api/providers` was
 * missing from.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { api, ApiError } from '../api';
import { resetSessionGuard } from '../sessionGuard';

const PROBE = '/api/auth/session';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('ApiClient.request 401 handling', () => {
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

  test('a stale NetMind token does not end the session', async () => {
    // The endpoint the 8/2 logs actually show 401ing.
    const fetchMock = vi.fn(async () =>
      jsonResponse(401, {
        detail: 'NetMind token invalid or expired',
        code: 'netmind_token_invalid',
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.getJobs('agent_1')).rejects.toBeInstanceOf(ApiError);

    expect(fetchMock.mock.calls.some(([u]) => String(u).includes(PROBE))).toBe(false);
    expect(dispatched).toHaveLength(0);
  });

  test('an expired JWT ends the session — after the probe agrees', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(401, { detail: 'Token expired', code: 'token_expired' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.getJobs('agent_1')).rejects.toBeInstanceOf(ApiError);
    // The dispatch happens on the probe's microtask, not the caller's.
    await vi.waitFor(() => expect(dispatched).toHaveLength(1));
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes(PROBE))).toBe(true);
  });

  test('a 401 the backend did not classify never ends the session', async () => {
    // Old backend, third-party proxy, misconfigured gateway — a bare 401
    // with no `code` must not be able to log anyone out.
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, { detail: 'Unauthorized' })));

    await expect(api.getJobs('agent_1')).rejects.toBeInstanceOf(ApiError);
    expect(dispatched).toHaveLength(0);
  });

  test('the thrown ApiError still carries status and detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(401, { detail: 'Token expired', code: 'token_expired' })),
    );

    const err = await api.getJobs('agent_1').catch((e) => e as ApiError);
    expect(err.status).toBe(401);
    expect(err.message).toContain('Token expired');
  });
});
