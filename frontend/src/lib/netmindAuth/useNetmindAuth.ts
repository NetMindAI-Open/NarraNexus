/**
 * @file_name: useNetmindAuth.ts
 * @description: NetMind login orchestration for the cloud login page.
 * Three entry actions — emailLogin, OAuth (popup + postMessage), and
 * bandType binding — all converge on `api.netmindLogin(loginToken)` which
 * trades the NetMind loginToken for our own JWT. The caller's onSuccess
 * receives the backend response AND the raw loginToken (to stash for
 * Phase 2/3). reCAPTCHA is intentionally absent: ckType=2 skips it.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { NetmindLoginResponse } from '@/types/api';
import { netmindPost } from './request';
import { baseRequestParams } from './constants';
import { getNetmindConfig } from '@/lib/runtimeConfig';
import { isTauri, openNetmindOAuth, takeNetmindOAuthResult } from '@/lib/tauri';
import { encryptPassword, generateRandomString } from './crypto';
import type { AuthBindInfo, NetmindUser } from './types';

/** Decode a bridged OAuth payload — the Rust side may hand back either the
 * URI-encoded fragment (opener-shim path) or a plain JSON string (URL-match
 * path). decodeURIComponent is a safe no-op on plain JSON (no % escapes). */
function decodeOAuthPayload(
  raw: string,
): { type?: string; code?: string; state?: string } | null {
  let s = raw;
  try {
    s = decodeURIComponent(raw);
  } catch {
    s = raw; // stray % in plain JSON — parse as-is
  }
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

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
  // Always-current handleAuthCallback for the desktop poll loop (which is set up
  // in startOAuth, defined before handleAuthCallback below).
  const handleAuthCallbackRef = useRef<
    ((code: string, state: string) => Promise<void>) | null
  >(null);
  // Guards the desktop OAuth poll interval so a second click doesn't stack loops.
  const oauthPollRef = useRef<number | null>(null);

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
    const url = `${accountsUrl}/auth.html?authApi=${authApi}/user/loginMsg/${type}`;
    // Browser: WKWebView blocks window.open + cross-window postMessage, so this
    // path is browser-only — keep the popup + `message` listener below.
    if (!isTauri()) {
      window.open(url, '', 'popup=1,width=600,height=650');
      return;
    }
    // Desktop: the Rust bridge opens auth.html in a child webview and buffers
    // the {code,state} result. Poll for it (delivery that doesn't depend on a
    // live Tauri event listener). Stop on first result or after ~3 min.
    void openNetmindOAuth(url);
    if (oauthPollRef.current !== null) {
      window.clearInterval(oauthPollRef.current);
    }
    let elapsed = 0;
    const iv = window.setInterval(async () => {
      elapsed += 800;
      const raw = await takeNetmindOAuthResult();
      if (raw) {
        window.clearInterval(iv);
        oauthPollRef.current = null;
        const msg = decodeOAuthPayload(raw);
        if (msg?.type === 'auth' && msg.code && msg.state) {
          void handleAuthCallbackRef.current?.(msg.code, msg.state);
        }
      } else if (elapsed >= 180000) {
        window.clearInterval(iv);
        oauthPollRef.current = null;
      }
    }, 800);
    oauthPollRef.current = iv;
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
  // Keep the ref current so the desktop poll loop always calls the latest.
  handleAuthCallbackRef.current = handleAuthCallback;

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
    // Browser popup path: auth.html postMessages the result to window.opener.
    // (Desktop uses the Rust bridge + poll set up in startOAuth instead.)
    const onMessage = (e: MessageEvent) => {
      if (e.data?.type === 'auth' && e.data.code && e.data.state) {
        void handleAuthCallback(e.data.code, e.data.state);
      }
    };
    window.addEventListener('message', onMessage);
    return () => {
      window.removeEventListener('message', onMessage);
      if (oauthPollRef.current !== null) {
        window.clearInterval(oauthPollRef.current);
        oauthPollRef.current = null;
      }
    };
  }, [handleAuthCallback]);

  return {
    loading, error, bindInfo,
    emailLogin, startOAuth, handleAuthCallback, submitBind, closeBind,
    sendResetCode, resetPassword,
  };
}
