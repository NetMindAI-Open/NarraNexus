import { afterEach, describe, expect, test, vi } from 'vitest';
import { api } from '../api';

afterEach(() => vi.restoreAllMocks());

describe('api.netmindLogin', () => {
  test('POSTs netmind_token + source to /api/auth/netmind-login', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ success: true, user_id: 'code', token: 'jwt' }),
    } as Response);
    const res = await api.netmindLogin('nm-token', 'arena');
    expect(res.success).toBe(true);
    expect(res.user_id).toBe('code');
    const [url, init] = f.mock.calls[0];
    expect(String(url)).toContain('/api/auth/netmind-login');
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      netmind_token: 'nm-token', source: 'arena',
    });
  });
});
