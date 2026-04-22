/**
 * LarkConfig — Per-agent Lark/Feishu bot binding configuration.
 *
 * States:
 *   1. No bot bound → show bind form (App ID, Secret, Platform)
 *   2. Bot bound, not logged in → show login button
 *   3. Bot bound, logged in → show connected status + unbind
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { MessageSquare, Link, Unlink, ExternalLink, Loader2, CheckCircle, AlertCircle, RefreshCw } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Input } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import type { LarkCredentialData } from '@/types';

const POLLING_INTERVAL_MS = 3000;
const POLLING_TIMEOUT_MS = 5 * 60 * 1000;

export function LarkConfig() {
  const { agentId } = useConfigStore();

  const [credential, setCredential] = useState<LarkCredentialData | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState('');

  // Bind form state
  const [appId, setAppId] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [ownerEmail, setOwnerEmail] = useState('');
  const [brand, setBrand] = useState<'feishu' | 'lark'>('feishu');

  // OAuth state
  const [authUrl, setAuthUrl] = useState('');
  const [polling, setPolling] = useState(false);
  const pollingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const fetchCredential = useCallback(async () => {
    if (!agentId) return;
    try {
      setLoading(true);
      setError('');
      const res = await api.getLarkCredential(agentId);
      if (!mountedRef.current) return;
      if (res.success) {
        setCredential(res.data || null);
      } else {
        setError(res.error || 'Failed to load credential');
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : 'Failed to fetch Lark credential');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [agentId]);

  // Reset all state on agent change
  useEffect(() => {
    setError('');
    setCredential(null);
    setAppId('');
    setAppSecret('');
    setOwnerEmail('');
    setBrand('feishu');
    setAuthUrl('');
    setPolling(false);
    fetchCredential();
  }, [fetchCredential]);

  // Track mount state for async safety
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current);
      if (pollingTimeoutRef.current) clearTimeout(pollingTimeoutRef.current);
    };
  }, [fetchCredential]);

  // Bind bot
  const handleBind = async () => {
    if (!agentId || !appId || !appSecret) return;
    setActionLoading(true);
    setError('');
    try {
      const res = await api.bindLarkBot(agentId, appId, appSecret, brand, ownerEmail);
      if (!mountedRef.current) return;
      if (res.success) {
        setAppId('');
        setAppSecret('');
        setOwnerEmail('');
        await fetchCredential();
      } else {
        setError(res.error || 'Failed to bind bot');
      }
    } catch (e: unknown) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : 'Failed to bind bot');
    } finally {
      if (mountedRef.current) setActionLoading(false);
    }
  };

  // Start OAuth login
  const handleLogin = async () => {
    if (!agentId) return;
    setActionLoading(true);
    setError('');
    try {
      const res = await api.larkAuthLogin(agentId);
      if (!mountedRef.current) return;
      if (res.success && res.data) {
        const url = res.data.verification_url || res.data.verification_uri || '';
        const code = res.data.device_code || res.data.user_code || '';
        if (url) {
          setAuthUrl(url);
          window.open(url, '_blank', 'noopener,noreferrer');
          if (code) {
            startPolling(agentId, code);
          }
        }
      } else {
        setError(res.error || 'Failed to initiate login');
      }
    } catch (e: unknown) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : 'Failed to initiate login');
    } finally {
      if (mountedRef.current) setActionLoading(false);
    }
  };

  // Poll for OAuth completion — agentId passed explicitly to avoid stale closure
  const startPolling = (targetAgentId: string, code: string) => {
    if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current);
    if (pollingTimeoutRef.current) clearTimeout(pollingTimeoutRef.current);

    setPolling(true);
    pollingIntervalRef.current = setInterval(async () => {
      try {
        const res = await api.larkAuthComplete(targetAgentId, code);
        if (!mountedRef.current) return;
        if (res.success) {
          if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current);
          if (pollingTimeoutRef.current) clearTimeout(pollingTimeoutRef.current);
          pollingIntervalRef.current = null;
          pollingTimeoutRef.current = null;
          setPolling(false);
          setAuthUrl('');
          await fetchCredential();
        }
      } catch {
        // Keep polling — auth not complete yet
      }
    }, POLLING_INTERVAL_MS);

    pollingTimeoutRef.current = setTimeout(() => {
      if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
      pollingTimeoutRef.current = null;
      if (mountedRef.current) setPolling(false);
    }, POLLING_TIMEOUT_MS);
  };

  // Unbind bot
  const handleUnbind = async () => {
    if (!agentId) return;
    if (!window.confirm('Unbind this Lark bot? This will remove all Lark inbox data for this agent.')) return;
    setActionLoading(true);
    setError('');
    try {
      const res = await api.unbindLarkBot(agentId);
      if (!mountedRef.current) return;
      if (res.success) {
        setCredential(null);
      } else {
        setError(res.error || 'Failed to unbind');
      }
    } catch (e: unknown) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : 'Failed to unbind');
    } finally {
      if (mountedRef.current) setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><MessageSquare className="w-4 h-4" /> Lark / Feishu</CardTitle></CardHeader>
        <CardContent><div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]"><Loader2 className="w-4 h-4 animate-spin" /> Loading...</div></CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageSquare className="w-4 h-4" />
          Lark / Feishu
        </CardTitle>
        <button
          onClick={() => fetchCredential()}
          disabled={loading}
          className="p-1 rounded hover:bg-[var(--bg-tertiary)] transition-colors text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          title="Refresh"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && (
          <div role="alert" className="flex items-center gap-2 text-sm text-[var(--color-red-500)] border border-[var(--color-red-500)] p-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
            {error}
          </div>
        )}

        {/* State 1: No bot bound */}
        {!credential && (
          <div className="space-y-3">
            <p className="text-xs text-[var(--text-secondary)]">
              Bind a Feishu/Lark bot to enable messaging, contacts, docs, calendar, and tasks.
            </p>
            <div className="space-y-2">
              <label className="block">
                <span className="sr-only">App ID</span>
                <Input
                  placeholder="App ID (e.g. cli_xxx)"
                  value={appId}
                  onChange={(e) => setAppId(e.target.value)}
                  className="text-sm"
                  aria-label="App ID"
                />
              </label>
              <label className="block">
                <span className="sr-only">App Secret</span>
                <Input
                  type="password"
                  placeholder="App Secret"
                  value={appSecret}
                  onChange={(e) => setAppSecret(e.target.value)}
                  className="text-sm"
                  aria-label="App Secret"
                />
              </label>
              <label className="block">
                <span className="sr-only">Owner email</span>
                <Input
                  placeholder="Your Lark account email"
                  value={ownerEmail}
                  onChange={(e) => setOwnerEmail(e.target.value)}
                  className="text-sm"
                  aria-label="Owner email"
                />
              </label>
              <div className="flex gap-2" role="group" aria-label="Select platform">
                <button
                  onClick={() => setBrand('feishu')}
                  aria-pressed={brand === 'feishu'}
                  className={`flex-1 py-1.5 px-3 text-xs rounded border transition-colors ${
                    brand === 'feishu'
                      ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]'
                      : 'border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--text-secondary)]'
                  }`}
                >
                  Feishu
                </button>
                <button
                  onClick={() => setBrand('lark')}
                  aria-pressed={brand === 'lark'}
                  className={`flex-1 py-1.5 px-3 text-xs rounded border transition-colors ${
                    brand === 'lark'
                      ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]'
                      : 'border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--text-secondary)]'
                  }`}
                >
                  Lark (International)
                </button>
              </div>
            </div>
            <Button
              onClick={handleBind}
              disabled={actionLoading || !appId || !appSecret}
              className="w-full"
              size="sm"
            >
              {actionLoading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Link className="w-4 h-4 mr-2" />}
              Bind Bot
            </Button>
          </div>
        )}

        {/* State 2: Bot bound, bot_ready (Bot works, OAuth not done) */}
        {credential && credential.auth_status === 'bot_ready' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="text-[var(--text-primary)] font-medium">
                  {credential.bot_name || credential.app_id}
                </span>
                <span className="text-[var(--text-secondary)] ml-2">
                  ({credential.brand === 'feishu' ? 'Feishu' : 'Lark'})
                </span>
              </div>
              <span className="flex items-center gap-1 text-xs text-[var(--color-green-500)]">
                <CheckCircle className="w-3 h-3" aria-hidden="true" /> Bot Connected
              </span>
            </div>

            {credential.owner_name && (
              <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)] bg-[var(--bg-tertiary)] p-2 rounded">
                <CheckCircle className="w-3 h-3 text-[var(--color-green-500)] flex-shrink-0" aria-hidden="true" />
                Linked as: <span className="text-[var(--text-primary)] font-medium">{credential.owner_name}</span>
              </div>
            )}

            <div className="text-xs text-[var(--text-secondary)]">
              App ID: {credential.app_id}
            </div>

            <div className="text-xs text-[var(--text-secondary)] border border-[var(--color-yellow-500)] p-2">
              Complete OAuth to unlock search features (contacts by name, messages, documents)
            </div>

            {polling ? (
              <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <Loader2 className="w-4 h-4 animate-spin" />
                Waiting for authorization...
                {authUrl && (
                  <a href={authUrl} target="_blank" rel="noopener noreferrer" className="text-[var(--accent-primary)] hover:underline">
                    <ExternalLink className="w-3 h-3 inline" aria-hidden="true" /> Open
                  </a>
                )}
              </div>
            ) : (
              <Button onClick={handleLogin} disabled={actionLoading} size="sm" className="w-full">
                {actionLoading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <ExternalLink className="w-4 h-4 mr-2" />}
                Login with {credential.brand === 'feishu' ? 'Feishu' : 'Lark'}
              </Button>
            )}

            <Button onClick={handleUnbind} disabled={actionLoading} variant="ghost" size="sm" className="w-full text-[var(--color-red-500)] hover:text-[var(--color-red-400)]">
              <Unlink className="w-4 h-4 mr-2" /> Unbind
            </Button>
          </div>
        )}

        {/* State 3: Bot bound, user_logged_in (fully connected) */}
        {credential && credential.auth_status === 'user_logged_in' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="text-[var(--text-primary)] font-medium">
                  {credential.bot_name || credential.app_id}
                </span>
                <span className="text-[var(--text-secondary)] ml-2">
                  ({credential.brand === 'feishu' ? 'Feishu' : 'Lark'})
                </span>
              </div>
              <span className="flex items-center gap-1 text-xs text-[var(--color-green-500)]">
                <CheckCircle className="w-3 h-3" aria-hidden="true" /> Fully Connected
              </span>
            </div>

            {credential.owner_name && (
              <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)] bg-[var(--bg-tertiary)] p-2 rounded">
                <CheckCircle className="w-3 h-3 text-[var(--color-green-500)] flex-shrink-0" aria-hidden="true" />
                Linked as: <span className="text-[var(--text-primary)] font-medium">{credential.owner_name}</span>
              </div>
            )}

            <div className="text-xs text-[var(--text-secondary)]">
              App ID: {credential.app_id}
            </div>

            <Button onClick={handleUnbind} disabled={actionLoading} variant="ghost" size="sm" className="w-full text-[var(--color-red-500)] hover:text-[var(--color-red-400)]">
              <Unlink className="w-4 h-4 mr-2" /> Unbind
            </Button>
          </div>
        )}

        {/* State 4: Bot bound, expired or not_logged_in */}
        {credential && (credential.auth_status === 'expired' || credential.auth_status === 'not_logged_in') && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="text-[var(--text-primary)]">{credential.app_id}</span>
                <span className="text-[var(--text-secondary)] ml-2">({credential.brand})</span>
              </div>
              <span className="flex items-center gap-1 text-xs text-[var(--color-yellow-500)]">
                <AlertCircle className="w-3 h-3" aria-hidden="true" /> {credential.auth_status === 'expired' ? 'Expired' : 'Not active'}
              </span>
            </div>

            <Button onClick={handleUnbind} disabled={actionLoading} variant="ghost" size="sm" className="w-full text-[var(--color-red-500)] hover:text-[var(--color-red-400)]">
              <Unlink className="w-4 h-4 mr-2" /> Unbind & Re-bind
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
