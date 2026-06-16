import { afterEach, describe, expect, test, vi } from 'vitest';
import { netmindPost } from '../request';

afterEach(() => { vi.restoreAllMocks(); delete (window as { __NARRANEXUS_CONFIG__?: unknown }).__NARRANEXUS_CONFIG__; });

function mockFetch(json: unknown, ok = true) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok, json: async () => json,
  } as Response);
}

describe('netmindPost', () => {
  test('posts form-urlencoded to authApi + path and unwraps data', async () => {
    (window as { __NARRANEXUS_CONFIG__?: unknown }).__NARRANEXUS_CONFIG__ =
      { netmindAuthApi: 'https://nm.test' };
    const f = mockFetch({ success: true, data: { loginToken: 'tok' } });
    const out = await netmindPost('/user/emailLogin', { email: 'a@b.com', n: 1 });
    expect(out).toEqual({ loginToken: 'tok' });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe('https://nm.test/user/emailLogin');
    expect((init as RequestInit).method).toBe('POST');
    expect((init as RequestInit).headers).toMatchObject({
      'Content-Type': 'application/x-www-form-urlencoded',
    });
    expect(String((init as RequestInit).body)).toContain('email=a%40b.com');
  });

  test('rejects on business success:false', async () => {
    (window as { __NARRANEXUS_CONFIG__?: unknown }).__NARRANEXUS_CONFIG__ =
      { netmindAuthApi: 'https://nm.test' };
    mockFetch({ success: false, msg: 'bad creds' });
    await expect(netmindPost('/user/emailLogin', {})).rejects.toThrow('bad creds');
  });
});
