/**
 * @file_name: useNetmindAuth.emailLogin.test.ts
 * @description: Login failures show the RIGHT generic message.
 *
 * An upstream credential rejection (NetmindApiError) is masked to one generic
 * "invalid email or password" so NetMind's distinct "user not found" vs "wrong
 * password" text can't enumerate registered emails. A TRANSPORT failure
 * (offline / 502) must instead show "connection failed" — not falsely tell the
 * user their password is wrong. Either way the real message reaches the funnel.
 */
import { act, renderHook } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const netmindPost = vi.fn();
// Preserve the real NetmindApiError (the hook does `instanceof`); override only the call.
vi.mock('../request', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../request')>()),
  netmindPost: (...a: unknown[]) => netmindPost(...a),
}));

const reportAuthFunnel = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { reportAuthFunnel: (...a: unknown[]) => reportAuthFunnel(...a) },
}));

vi.mock('../crypto', () => ({
  encryptPassword: () => 'enc',
  generateRandomString: () => 'rand',
}));

import { NetmindApiError } from '../request';
import { useNetmindAuth } from '../useNetmindAuth';

beforeEach(() => {
  netmindPost.mockReset();
  reportAuthFunnel.mockReset();
});

test('an upstream rejection shows a generic message, not the enumerating text', async () => {
  netmindPost.mockRejectedValue(new NetmindApiError('User not found'));
  const { result } = renderHook(() => useNetmindAuth({ onSuccess: vi.fn() }));

  await act(async () => {
    await result.current.emailLogin('probe@example.com', 'whatever');
  });

  // The upstream "User not found" must NOT reach the user (no account enumeration).
  expect(result.current.error).toBeTruthy();
  expect(result.current.error).not.toMatch(/user not found/i);
  // But the real message still goes to the funnel for internal diagnosis.
  expect(reportAuthFunnel).toHaveBeenCalledWith(
    'netmind_email_login_failed',
    'probe@example.com',
    'User not found',
  );
});

test('an upstream 200 with no token is a protocol break → connectionFailed, not invalidCredentials', async () => {
  // Must NOT tell the user their password is wrong (which would push them into
  // the reset flow for a password that was never the problem).
  netmindPost.mockResolvedValue({}); // success shape but no loginToken
  const { result } = renderHook(() => useNetmindAuth({ onSuccess: vi.fn() }));
  await act(async () => { await result.current.emailLogin('a@b.com', 'pw'); });
  expect(result.current.error).toMatch(/pages\.login\.connectionFailed|connection failed/i);
  expect(result.current.error).not.toMatch(/pages\.login\.invalidCredentials|invalid email or password/i);
});

test('a transport failure shows connection-failed, not a bogus credential error', async () => {
  // A bare Error is what fetch/offline/JSON-parse throw — NOT a NetmindApiError.
  netmindPost.mockRejectedValue(new Error('Failed to fetch'));
  const { result } = renderHook(() => useNetmindAuth({ onSuccess: vi.fn() }));

  await act(async () => {
    await result.current.emailLogin('probe@example.com', 'whatever');
  });

  // Must NOT read as a credential error; the connectionFailed key is shown.
  expect(result.current.error).toMatch(/pages\.login\.connectionFailed|connection failed/i);
  expect(result.current.error).not.toMatch(/pages\.login\.invalidCredentials|invalid email or password/i);
});
