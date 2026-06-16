/**
 * @file_name: useNetmindAuth.ts
 * @description: NetMind login orchestration for the cloud login page.
 * Three entry actions — emailLogin, OAuth (popup + postMessage), and
 * bandType binding — all converge on `api.netmindLogin(loginToken)` which
 * trades the NetMind loginToken for our own JWT. The caller's onSuccess
 * receives the backend response AND the raw loginToken (to stash for
 * Phase 2/3). reCAPTCHA is intentionally absent: ckType=2 skips it.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { NetmindLoginResponse } from '@/types/api';
import { netmindPost } from './request';
import { baseRequestParams } from './constants';
import { getNetmindConfig } from '@/lib/runtimeConfig';
import { encryptPassword, generateRandomString } from './crypto';
import type { AuthBindInfo, NetmindUser } from './types';

type OAuthType = 'GOOGLE' | 'MICROSOFT' | 'GITHUB';

interface NetmindLoginPayload { loginToken?: string; user?: NetmindUser }

interface Options {
  source?: string;
  onSuccess?: (res: NetmindLoginResponse, loginToken: string) => void;
}

export function useNetmindAuth({ source, onSuccess }: Options = {}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [bindInfo, setBindInfo] = useState<AuthBindInfo | null>(null);

  const exchange = useCallback(
    async (loginToken: string) => {
      const res = await api.netmindLogin(loginToken, source);
      onSuccess?.(res, loginToken);
    },
    [source, onSuccess],
  );

  const emailLogin = useCallback(
    async (email: string, password: string) => {
      setLoading(true);
      setError('');
      try {
        const signStr = generateRandomString();
        const data = await netmindPost<NetmindLoginPayload>('/user/emailLogin', {
          ...baseRequestParams(),
          email,
          password: encryptPassword(password, signStr),
          signStr,
          ckType: 2,
        });
        if (!data.loginToken) throw new Error('Login failed');
        await exchange(data.loginToken);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Login failed');
      } finally {
        setLoading(false);
      }
    },
    [exchange],
  );

  const startOAuth = useCallback((type: OAuthType) => {
    const { accountsUrl, authApi } = getNetmindConfig();
    sessionStorage.setItem('nm-oauth-type', type);
    window.open(
      `${accountsUrl}/auth.html?authApi=${authApi}/user/loginMsg/${type}`,
      '',
      'popup=1,width=600,height=650',
    );
  }, []);

  const handleAuthCallback = useCallback(
    async (code: string, state: string) => {
      setLoading(true);
      setError('');
      try {
        const data = await netmindPost<NetmindLoginPayload & AuthBindInfo>(
          '/user/userCallBack',
          {
            ...baseRequestParams(),
            authCallbackStr: JSON.stringify({ code, state }),
            oauthType: sessionStorage.getItem('nm-oauth-type') || '',
          },
        );
        if (data.loginToken) {
          await exchange(data.loginToken);
        } else {
          setBindInfo(data as AuthBindInfo);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : 'OAuth failed');
      } finally {
        setLoading(false);
      }
    },
    [exchange],
  );

  const submitBind = useCallback(
    async (extra: { email?: string; verifyCode?: string } = {}) => {
      if (!bindInfo) return;
      setLoading(true);
      setError('');
      try {
        const params: Record<string, unknown> = {
          ...baseRequestParams(),
          bandType: bindInfo.bandType,
          identifyCode: bindInfo.identifyCode,
          email: bindInfo.thirdEmail || bindInfo.canBandEmail,
        };
        if (bindInfo.bandType === 1) {
          params.email = extra.email;
          params.verifyCode = extra.verifyCode;
        }
        const data = await netmindPost<NetmindLoginPayload>('/user/userCallBack', params);
        if (!data.loginToken) throw new Error('Bind failed');
        setBindInfo(null);
        await exchange(data.loginToken);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Bind failed');
      } finally {
        setLoading(false);
      }
    },
    [bindInfo, exchange],
  );

  const closeBind = useCallback(() => setBindInfo(null), []);

  // Forgot-password (NetMind). Two-step, no reCAPTCHA: send a code to the
  // email (sendCode type=2), then reset with code + new password. Both call
  // NetMind directly, like emailLogin. Return true on success so the UI can
  // advance to the next step / close.
  const sendResetCode = useCallback(async (email: string): Promise<boolean> => {
    setLoading(true);
    setError('');
    try {
      await netmindPost('/register/sendCode', { ...baseRequestParams(), email, type: 2 });
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to send code');
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  const resetPassword = useCallback(
    async (email: string, code: string, newPassword: string): Promise<boolean> => {
      setLoading(true);
      setError('');
      try {
        await netmindPost('/user/resetPassword', {
          ...baseRequestParams(), email, code, newPassword,
        });
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to reset password');
        return false;
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.data?.type === 'auth' && e.data.code && e.data.state) {
        void handleAuthCallback(e.data.code, e.data.state);
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [handleAuthCallback]);

  return {
    loading, error, bindInfo,
    emailLogin, startOAuth, handleAuthCallback, submitBind, closeBind,
    sendResetCode, resetPassword,
  };
}
