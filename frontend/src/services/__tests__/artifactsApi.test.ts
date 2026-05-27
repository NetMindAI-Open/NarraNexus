/**
 * Pin the URL-resolution contract of artifactsApi.
 *
 * Root cause this test guards against: pre-fix every fetch in
 * artifactsApi used bare relative URLs (`/api/agents/...`). In the
 * cloud SaaS deployment that works (page origin == backend origin).
 * In the Tauri dmg the page is served from `tauri.localhost` while
 * the backend listens on `http://localhost:8000` — so a bare
 * `/api/agents/...` would resolve to `tauri.localhost/api/...` and
 * 404 silently, leaving every user-visible symptom:
 *  - artifact list shows empty (listSession / listPinned 404)
 *  - clicking an artifact does nothing (getRawUrl / heal 404)
 *  - the panel never opens (zero items → sliver collapse)
 *
 * Contract: every fetch URL is built by prepending `getApiBaseUrl()`
 * (the app-wide base resolver in runtimeStore). It returns `''` in
 * cloud (preserving relative URLs) and `http://localhost:8000` in
 * dmg local mode. Same code, both deployments work.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Hoisted spy that every test can re-stub.
const getApiBaseUrlSpy = vi.fn(() => '');
vi.mock('@/stores/runtimeStore', () => ({
  getApiBaseUrl: () => getApiBaseUrlSpy(),
}));

// Import AFTER the mock so artifactsApi sees the mocked module.
import { artifactsApi, authHeaders } from '../artifactsApi';

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  getApiBaseUrlSpy.mockReset();
  getApiBaseUrlSpy.mockReturnValue('');
  vi.stubGlobal('fetch', fetchMock);
  // Default response: JSON body OK. Each test can override.
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  // Wipe any persisted auth between tests so authHeaders() returns clean.
  try {
    window.localStorage.removeItem('narra-nexus-config');
  } catch {
    /* jsdom may not allow */
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function urlOf(call: number): string {
  const args = fetchMock.mock.calls[call];
  expect(args, `expected fetch call #${call}`).toBeDefined();
  return String(args[0]);
}

describe('artifactsApi URL resolution', () => {
  describe('cloud mode (getApiBaseUrl returns "")', () => {
    beforeEach(() => {
      getApiBaseUrlSpy.mockReturnValue('');
    });

    it('listSession uses bare relative URL', async () => {
      await artifactsApi.listSession('agent_x', 'sess_1');
      expect(urlOf(0)).toBe('/api/agents/agent_x/artifacts?scope=session&session_id=sess_1');
    });

    it('listPinned uses bare relative URL', async () => {
      await artifactsApi.listPinned('agent_x');
      expect(urlOf(0)).toBe('/api/agents/agent_x/artifacts?scope=pinned');
    });

    it('getDetail uses bare relative URL', async () => {
      await artifactsApi.getDetail('agent_x', 'art_1');
      expect(urlOf(0)).toBe('/api/agents/agent_x/artifacts/art_1');
    });

    it('listAll (user-scoped) uses bare relative URL', async () => {
      await artifactsApi.listAll('user_x');
      expect(urlOf(0)).toBe('/api/users/user_x/artifacts');
    });
  });

  describe('dmg local mode (getApiBaseUrl returns http://localhost:8000)', () => {
    beforeEach(() => {
      getApiBaseUrlSpy.mockReturnValue('http://localhost:8000');
    });

    it('listSession prefixes the absolute backend URL', async () => {
      await artifactsApi.listSession('agent_x', 'sess_1');
      expect(urlOf(0)).toBe(
        'http://localhost:8000/api/agents/agent_x/artifacts?scope=session&session_id=sess_1',
      );
    });

    it('listPinned prefixes the absolute backend URL', async () => {
      await artifactsApi.listPinned('agent_x');
      expect(urlOf(0)).toBe('http://localhost:8000/api/agents/agent_x/artifacts?scope=pinned');
    });

    it('getDetail prefixes the absolute backend URL', async () => {
      await artifactsApi.getDetail('agent_x', 'art_1');
      expect(urlOf(0)).toBe('http://localhost:8000/api/agents/agent_x/artifacts/art_1');
    });

    it('setPinned prefixes the absolute backend URL', async () => {
      await artifactsApi.setPinned('agent_x', 'art_1', true);
      expect(urlOf(0)).toBe('http://localhost:8000/api/agents/agent_x/artifacts/art_1');
      // method check kept light — main contract is the URL
      const init = fetchMock.mock.calls[0][1];
      expect(init.method).toBe('PATCH');
    });

    it('heal prefixes the absolute backend URL', async () => {
      await artifactsApi.heal('agent_x', 'art_1');
      expect(urlOf(0)).toBe('http://localhost:8000/api/agents/agent_x/artifacts/art_1/heal');
    });

    it('remove prefixes the absolute backend URL', async () => {
      // 204 No Content — body parsing is skipped in the implementation.
      fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
      await artifactsApi.remove('agent_x', 'art_1');
      expect(urlOf(0)).toBe('http://localhost:8000/api/agents/agent_x/artifacts/art_1');
    });

    it('registerFromWorkspace prefixes the absolute backend URL', async () => {
      await artifactsApi.registerFromWorkspace('agent_x', {
        file_path: 'foo/bar.html',
        kind: 'html',
      });
      expect(urlOf(0)).toBe('http://localhost:8000/api/agents/agent_x/artifacts/register');
    });

    it('listAll (user-scoped) prefixes the absolute backend URL', async () => {
      await artifactsApi.listAll('user_x');
      expect(urlOf(0)).toBe('http://localhost:8000/api/users/user_x/artifacts');
    });

    it('bulkDelete (user-scoped) prefixes the absolute backend URL', async () => {
      await artifactsApi.bulkDelete('user_x', ['a', 'b']);
      expect(urlOf(0)).toBe('http://localhost:8000/api/users/user_x/artifacts');
    });
  });

  describe('getRawUrl absolutizes the backend-returned raw_url', () => {
    // Backend returns raw_url as a path like "/api/public/artifacts/raw/<token>/".
    // In dmg the page can't load that as iframe src or fetch it without the
    // backend host — so getRawUrl must convert it to absolute before handing
    // it to renderers.
    beforeEach(() => {
      getApiBaseUrlSpy.mockReturnValue('http://localhost:8000');
      fetchMock.mockResolvedValue(
        new Response(
          JSON.stringify({
            token: 'tok123',
            raw_url: '/api/public/artifacts/raw/tok123/',
            expires_at: 9999,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      );
    });

    it('returns an absolute URL in dmg mode', async () => {
      const u = await artifactsApi.getRawUrl('agent_x', 'art_1');
      expect(u).toBe('http://localhost:8000/api/public/artifacts/raw/tok123/');
    });

    it('passes through unchanged in cloud mode (relative is fine)', async () => {
      getApiBaseUrlSpy.mockReturnValue('');
      const u = await artifactsApi.getRawUrl('agent_x', 'art_1');
      expect(u).toBe('/api/public/artifacts/raw/tok123/');
    });

    it('leaves already-absolute backend URLs alone', async () => {
      // Defensive: if the backend ever returns a fully-qualified raw_url
      // (e.g. CDN-hosted artifact in some future SaaS variant), don't
      // double-prefix it.
      fetchMock.mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            token: 'tok123',
            raw_url: 'https://cdn.example/raw/tok123/',
            expires_at: 9999,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      );
      const u = await artifactsApi.getRawUrl('agent_x', 'art_1');
      expect(u).toBe('https://cdn.example/raw/tok123/');
    });
  });
});

describe('authHeaders', () => {
  // Sanity: the X-User-Id branch is what local-mode auth actually requires;
  // make sure refactoring base() doesn't disturb header building.
  it('emits Authorization + X-User-Id when both are persisted', () => {
    window.localStorage.setItem(
      'narra-nexus-config',
      JSON.stringify({ state: { token: 'jwt-abc', userId: 'usr_42' } }),
    );
    const h = authHeaders();
    expect(h['Authorization']).toBe('Bearer jwt-abc');
    expect(h['X-User-Id']).toBe('usr_42');
  });

  it('returns empty object when no config is persisted', () => {
    expect(authHeaders()).toEqual({});
  });
});
