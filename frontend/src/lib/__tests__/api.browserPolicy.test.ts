/**
 * @file_name: api.browserPolicy.test.ts
 * @description: Browser policy transport uses authenticated requests and narrow mutation bodies.
 */
import { afterEach, expect, test, vi } from 'vitest';
import { api } from '../api';

afterEach(() => vi.restoreAllMocks());

test('runtime source selection uses authenticated PUT and sends only the source', async () => {
  vi.spyOn(api, 'getAuthHeaders').mockReturnValue({ 'X-User-Id': 'owner' });
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ state: 'ready' })));
  await api.setBrowserSource('system');
  const [url, init] = fetch.mock.calls[0];
  expect(String(url)).toContain('/api/browser/runtime/source');
  expect(init?.method).toBe('PUT');
  expect(init?.headers).toMatchObject({ 'X-User-Id': 'owner' });
  expect(JSON.parse(init?.body as string)).toEqual({ source: 'system' });
});

const view = { agent_id: 'agent / one', defaults: { full_cdp_access: 'deny' }, origins: [] };

test('runtime mode selection uses authenticated PUT and sends only the mode', async () => {
  vi.spyOn(api, 'getAuthHeaders').mockReturnValue({ 'X-User-Id': 'owner' });
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ state: 'ready' })));
  await api.setBrowserMode('headed');
  const [url, init] = fetch.mock.calls[0];
  expect(String(url)).toContain('/api/browser/runtime/mode');
  expect(init?.method).toBe('PUT');
  expect(init?.headers).toMatchObject({ 'X-User-Id': 'owner' });
  expect(JSON.parse(init?.body as string)).toEqual({ mode: 'headed' });
});

test('policy reads encode the agent and retain authentication headers', async () => {
  vi.spyOn(api, 'getAuthHeaders').mockReturnValue({ 'X-User-Id': 'owner' });
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(view)));
  expect(await api.getBrowserPolicy('agent / one')).toEqual(view);
  const [url, init] = fetch.mock.calls[0];
  expect(String(url)).toContain('/api/browser/policy/agent%20%2F%20one');
  expect(init?.headers).toMatchObject({ 'X-User-Id': 'owner' });
});

test('advanced-script updates send only the explicit origin capability and verdict', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(view)));
  const body = { origin: 'https://accounts.example.test', capability: 'full_cdp_access' as const, verdict: 'allow' as const };
  expect(await api.updateBrowserPolicy('agent / one', body)).toEqual(view);
  const [url, init] = fetch.mock.calls[0];
  expect(String(url)).toContain('/api/browser/policy/agent%20%2F%20one');
  expect(init?.method).toBe('PUT');
  expect(JSON.parse(init?.body as string)).toEqual(body);
});

test('revocation targets one origin and capability without fabricating scope IDs', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(view)));
  const body = { origin: 'https://accounts.example.test', capability: 'full_cdp_access' as const };
  expect(await api.revokeBrowserPolicy('agent / one', body)).toEqual(view);
  const [url, init] = fetch.mock.calls[0];
  expect(String(url)).toContain('/api/browser/policy/agent%20%2F%20one/revoke');
  expect(init?.method).toBe('POST');
  expect(JSON.parse(init?.body as string)).toEqual(body);
});

test('permission failures preserve the server error for recovery', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ detail: 'Permission store unavailable' }), { status: 503 }));
  await expect(api.getBrowserPolicy('agent')).rejects.toThrow('Permission store unavailable');
  await expect(api.revokeBrowserPolicy('agent', { origin: 'https://site.test', capability: 'full_cdp_access' }))
    .rejects.toThrow('Permission store unavailable');
});
