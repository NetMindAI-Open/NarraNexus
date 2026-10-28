import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { useNetmindAuth } from '../useNetmindAuth';
import { NetmindApiError } from '../request';

const netmindPost = vi.fn();
// Keep the real NetmindApiError (the hook does `instanceof`); override the call.
vi.mock('../request', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../request')>()),
  netmindPost: (...a: unknown[]) => netmindPost(...a),
}));
const netmindLogin = vi.fn();
const reportAuthFunnel = vi.fn();
vi.mock('@/lib/api', () => ({ api: {
  netmindLogin: (...a: unknown[]) => netmindLogin(...a),
  reportAuthFunnel: (...a: unknown[]) => reportAuthFunnel(...a),
} }));

afterEach(() => {
  netmindPost.mockReset();
  netmindLogin.mockReset();
  reportAuthFunnel.mockReset();
});

describe('useNetmindAuth.emailLogin', () => {
  test('encrypts, calls emailLogin, then exchanges loginToken via backend', async () => {
    netmindPost.mockResolvedValue({ loginToken: 'nm-tok', user: { userSystemCode: 'c', email: 'a@b' } });
    netmindLogin.mockResolvedValue({ success: true, user_id: 'c', token: 'jwt' });
    const onSuccess = vi.fn();
    const { result } = renderHook(() => useNetmindAuth({ onSuccess }));

    await act(async () => { await result.current.emailLogin('a@b.com', 'pw'); });

    expect(netmindPost).toHaveBeenCalledWith('/user/emailLogin', expect.objectContaining({
      email: 'a@b.com', ckType: 2,
    }));
    expect(netmindLogin).toHaveBeenCalledWith('nm-tok', undefined);
    expect(onSuccess).toHaveBeenCalledWith(expect.objectContaining({ success: true }), 'nm-tok');
  });

  test('surfaces emailLogin failure as a generic (non-enumerating) error state', async () => {
    // A real upstream rejection (NetmindApiError) is what gets masked.
    netmindPost.mockRejectedValue(new NetmindApiError('Invalid password'));
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.emailLogin('a@b.com', 'bad'); });
    // The upstream message is MASKED to prevent account enumeration
    // (see useNetmindAuth.emailLogin.test.ts); some error is still shown.
    expect(result.current.error).toBeTruthy();
    expect(result.current.error).not.toMatch(/invalid password/i);
    expect(netmindLogin).not.toHaveBeenCalled();
    // The browser->NetMind failure must be reported server-side — this call
    // is the only trace the funnel gets (Base recvre9LlfwXAP).
    expect(reportAuthFunnel).toHaveBeenCalledWith(
      'netmind_email_login_failed', 'a@b.com', 'Invalid password',
    );
  });

  test('a successful login reports nothing', async () => {
    netmindPost.mockResolvedValue({ loginToken: 'nm-tok' });
    netmindLogin.mockResolvedValue({ success: true });
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.emailLogin('a@b.com', 'pw'); });
    expect(reportAuthFunnel).not.toHaveBeenCalled();
  });

  test('a backend token-exchange failure surfaces the error but does NOT report', async () => {
    // The exchange hits OUR backend, which already logs the failure as
    // [login-funnel] — a client report would double-count it under the
    // wrong label (the NetMind step succeeded).
    netmindPost.mockResolvedValue({ loginToken: 'nm-tok' });
    netmindLogin.mockRejectedValue(new Error('exchange blew up'));
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.emailLogin('a@b.com', 'pw'); });
    expect(result.current.error).toBe('exchange blew up');
    expect(reportAuthFunnel).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });
});

describe('useNetmindAuth.handleAuthCallback reporting split', () => {
  test('a NetMind-direct callback rejection reports netmind_oauth_failed', async () => {
    // An upstream rejection (NetmindApiError) keeps its message; a transport
    // failure would show connectionFailed. Both still report to the funnel.
    netmindPost.mockRejectedValue(new NetmindApiError('oauth upstream down'));
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.handleAuthCallback('code', 'state'); });
    expect(result.current.error).toBe('oauth upstream down');
    expect(reportAuthFunnel).toHaveBeenCalledWith(
      'netmind_oauth_failed', undefined, 'oauth upstream down',
    );
  });

  test('a backend exchange failure after OAuth surfaces the error but does NOT report', async () => {
    // Same split as emailLogin: the NetMind step succeeded; the exchange
    // failure is already server-logged (Base recvre9LlfwXAP's "came back
    // from OAuth but token exchange died" bucket must not be mislabeled).
    netmindPost.mockResolvedValue({ loginToken: 'nm-tok' });
    netmindLogin.mockRejectedValue(new Error('exchange down'));
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.handleAuthCallback('code', 'state'); });
    expect(result.current.error).toBe('exchange down');
    expect(reportAuthFunnel).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });
});

describe('useNetmindAuth password reset', () => {
  test('sendResetCode posts to /register/sendCode with type=2 (forgot-password)', async () => {
    netmindPost.mockResolvedValue({});
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.sendResetCode('a@b.com'); });
    expect(netmindPost).toHaveBeenCalledWith('/register/sendCode', expect.objectContaining({
      email: 'a@b.com', type: 2,
    }));
  });

  test('resetPassword posts email+code+newPassword to /user/resetPassword', async () => {
    netmindPost.mockResolvedValue({});
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => {
      await result.current.resetPassword('a@b.com', '123456', 'NewPw1234');
    });
    expect(netmindPost).toHaveBeenCalledWith('/user/resetPassword', expect.objectContaining({
      email: 'a@b.com', code: '123456', newPassword: 'NewPw1234',
    }));
  });

  test('resetPassword masks an upstream rejection to one generic message (no enumeration)', async () => {
    // "Invalid code" and "email not registered" both map to the same generic —
    // echoing the latter verbatim would re-open the enumeration this flow closes.
    netmindPost.mockRejectedValue(new NetmindApiError('email not registered'));
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => {
      await result.current.resetPassword('a@b.com', 'bad', 'NewPw1234');
    });
    expect(result.current.error).not.toMatch(/not registered/i);
    expect(result.current.error).toMatch(/pages\.login\.resetCodeInvalid|invalid or has expired/i);
    // The real reason is this step's only server-side trace.
    expect(reportAuthFunnel).toHaveBeenCalledWith(
      'netmind_reset_password_failed', 'a@b.com', 'email not registered',
    );
  });

  test('resetPassword transport failure shows connectionFailed, not bare English', async () => {
    netmindPost.mockRejectedValue(new Error('Failed to fetch'));
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => {
      await result.current.resetPassword('a@b.com', '123456', 'NewPw1234');
    });
    expect(result.current.error).toMatch(/pages\.login\.connectionFailed|connection failed/i);
    expect(result.current.error).not.toMatch(/failed to fetch/i);
  });

  test('sendResetCode advances (returns true) on an upstream rejection — no boolean enumeration', async () => {
    netmindPost.mockRejectedValue(new NetmindApiError('email not registered'));
    const { result } = renderHook(() => useNetmindAuth());
    let advanced: boolean | undefined;
    await act(async () => { advanced = await result.current.sendResetCode('probe@x.com'); });
    // Registered vs not-registered must look identical: advance either way, no error.
    expect(advanced).toBe(true);
    expect(result.current.error).toBe('');
    // Real reason still reaches the funnel.
    expect(reportAuthFunnel).toHaveBeenCalledWith(
      'netmind_reset_code_failed', 'probe@x.com', 'email not registered',
    );
  });

  test('sendResetCode stops (returns false) only on a transport failure', async () => {
    netmindPost.mockRejectedValue(new Error('Failed to fetch'));
    const { result } = renderHook(() => useNetmindAuth());
    let advanced: boolean | undefined;
    await act(async () => { advanced = await result.current.sendResetCode('a@b.com'); });
    expect(advanced).toBe(false);
    expect(result.current.error).toMatch(/pages\.login\.connectionFailed|connection failed/i);
  });
});

describe('useNetmindAuth OAuth callback', () => {
  test('loginToken in callback exchanges via backend', async () => {
    netmindPost.mockResolvedValue({ loginToken: 'oauth-tok', user: { userSystemCode: 'c' } });
    netmindLogin.mockResolvedValue({ success: true, token: 'jwt' });
    const onSuccess = vi.fn();
    const { result } = renderHook(() => useNetmindAuth({ onSuccess }));
    await act(async () => { await result.current.handleAuthCallback('code', 'state'); });
    expect(netmindLogin).toHaveBeenCalledWith('oauth-tok', undefined);
  });

  test('no loginToken → exposes bind info', async () => {
    netmindPost.mockResolvedValue({ bandType: 2, identifyCode: 'idc', thirdEmail: 'x@y' });
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.handleAuthCallback('code', 'state'); });
    expect(result.current.bindInfo).toMatchObject({ bandType: 2, identifyCode: 'idc' });
  });

  test('window postMessage{type:auth} drives the OAuth callback', async () => {
    netmindPost.mockResolvedValue({ loginToken: 'msg-tok', user: { userSystemCode: 'c' } });
    netmindLogin.mockResolvedValue({ success: true, token: 'jwt' });
    renderHook(() => useNetmindAuth());
    await act(async () => {
      window.dispatchEvent(new MessageEvent('message', { data: { type: 'auth', code: 'c', state: 's' } }));
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => expect(netmindLogin).toHaveBeenCalled());
    expect(netmindLogin).toHaveBeenCalledWith('msg-tok', undefined);
  });
});

describe('useNetmindAuth.submitBind', () => {
  test('bandType===1 uses extra.email and extra.verifyCode, then exchanges', async () => {
    netmindPost
      .mockResolvedValueOnce({ bandType: 1, identifyCode: 'idc' })
      .mockResolvedValueOnce({ loginToken: 'bind-tok', user: { userSystemCode: 'c' } });
    netmindLogin.mockResolvedValue({ success: true, token: 'jwt' });
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.handleAuthCallback('code', 'state'); });
    await act(async () => { await result.current.submitBind({ email: 'e@x', verifyCode: '123' }); });
    const bindCallBody = netmindPost.mock.calls[1][1] as Record<string, unknown>;
    expect(bindCallBody).toMatchObject({ email: 'e@x', verifyCode: '123' });
    expect(netmindLogin).toHaveBeenCalledWith('bind-tok', undefined);
  });

  test('bandType===3 uses canBandEmail, omits verifyCode, then exchanges', async () => {
    netmindPost
      .mockResolvedValueOnce({ bandType: 3, identifyCode: 'idc', canBandEmail: 'me@x' })
      .mockResolvedValueOnce({ loginToken: 'bind-tok-3', user: { userSystemCode: 'c' } });
    netmindLogin.mockResolvedValue({ success: true, token: 'jwt' });
    const { result } = renderHook(() => useNetmindAuth());
    await act(async () => { await result.current.handleAuthCallback('code', 'state'); });
    await act(async () => { await result.current.submitBind(); });
    const bindCallBody = netmindPost.mock.calls[1][1] as Record<string, unknown>;
    expect(bindCallBody).toMatchObject({ email: 'me@x' });
    expect(bindCallBody).not.toHaveProperty('verifyCode');
    expect(netmindLogin).toHaveBeenCalledWith('bind-tok-3', undefined);
  });
});
