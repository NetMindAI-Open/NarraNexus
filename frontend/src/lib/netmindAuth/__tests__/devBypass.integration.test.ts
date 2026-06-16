/**
 * @file_name: devBypass.integration.test.ts
 * @description: Frontend → backend exchange contract for the dev-bypass
 * path. Real NetMind round-trips can't run from the dev workstation (AWS
 * network wall — see reference/auth/specs/phase1-frontend-login-migration.md
 * §8). This pins that the frontend forwards a dev-bypass token verbatim as
 * `netmind_token` to /api/auth/netmind-login, so a local smoke (backend with
 * NETMIND_DEV_BYPASS=1) exercises our whole stack without touching NetMind.
 */
import { afterEach, describe, expect, test, vi } from 'vitest';
import { api } from '@/lib/api';

afterEach(() => vi.restoreAllMocks());

describe('dev-bypass exchange contract', () => {
  test('dev-bypass token is forwarded verbatim as netmind_token', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        success: true,
        user_id: 'devbp_abc',
        token: 'jwt',
        is_new_user: true,
      }),
    } as Response);

    const res = await api.netmindLogin('dev-bypass-tester@narra.dev');

    expect(res.user_id).toBe('devbp_abc');
    expect(res.is_new_user).toBe(true);
    const body = JSON.parse(String((f.mock.calls[0][1] as RequestInit).body));
    expect(body.netmind_token).toBe('dev-bypass-tester@narra.dev');
  });
});
