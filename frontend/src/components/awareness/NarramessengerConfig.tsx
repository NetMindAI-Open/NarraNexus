/**
 * NarramessengerConfig — Per-agent NarraMessenger binding configuration.
 *
 * States:
 *   1. Not bound → paste the bind command/link (from the NarraMessenger app:
 *      My Space → My Agents → Bind Agents) + Bind button.
 *   2. Bound     → connected status (matrix_user_id, owner) + Unbind.
 *
 * Unlike Lark/Slack/Telegram there is no token-typing — the owner copies a
 * one-time bind link from the NarraMessenger app and pastes it; the backend
 * drives the Gateway bind (report-profile → connect) and writes the credential.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  MessageCircle,
  Link,
  Unlink,
  Loader2,
  CheckCircle,
  AlertCircle,
  RefreshCw,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';

import { Card, CardHeader, CardTitle, CardContent, Button, Input, useConfirm } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import type { NarramessengerCredentialData } from '@/types';

import type { ChannelConfigProps } from './IMChannelsSection';

export function NarramessengerConfig({ onBindStateChange }: ChannelConfigProps = {}) {
  const { agentId } = useConfigStore();

  const [credential, setCredential] = useState<NarramessengerCredentialData | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [unbindLoading, setUnbindLoading] = useState(false);
  const [error, setError] = useState('');

  const [bindCommand, setBindCommand] = useState('');
  const [setupOpen, setSetupOpen] = useState(false);

  const mountedRef = useRef(true);
  const { confirm, dialog: confirmDialog } = useConfirm();

  const fetchCredential = useCallback(async () => {
    if (!agentId) return;
    try {
      setLoading(true);
      setError('');
      const res = await api.getNarramessengerCredential(agentId);
      if (!mountedRef.current) return;
      if (res.success) {
        setCredential(res.data || null);
      } else {
        setError(res.error || 'Failed to load NarraMessenger credential');
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : 'Failed to fetch NarraMessenger credential');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    setError('');
    setCredential(null);
    setBindCommand('');
    fetchCredential();
  }, [fetchCredential]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const handleBind = async () => {
    if (!agentId || !bindCommand.trim()) return;
    setActionLoading(true);
    setError('');
    try {
      const res = await api.bindNarramessenger(agentId, bindCommand.trim());
      if (res.success) {
        setBindCommand('');
        await fetchCredential();
        onBindStateChange?.();
      } else {
        setError(res.error || 'Bind failed');
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Bind failed');
    } finally {
      if (mountedRef.current) setActionLoading(false);
    }
  };

  const handleUnbind = async () => {
    if (!agentId) return;
    const ok = await confirm({
      title: 'Unbind NarraMessenger?',
      message: 'The agent will stop receiving NarraMessenger messages until you bind again.',
      confirmText: 'Unbind',
      danger: true,
    });
    if (!ok) return;
    setUnbindLoading(true);
    setError('');
    try {
      const res = await api.unbindNarramessenger(agentId);
      if (res.success) {
        await fetchCredential();
        onBindStateChange?.();
      } else {
        setError(res.error || 'Unbind failed');
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unbind failed');
    } finally {
      if (mountedRef.current) setUnbindLoading(false);
    }
  };

  return (
    <Card>
      {confirmDialog}
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageCircle className="w-4 h-4" />
          NarraMessenger
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

        {/* State 1: Not bound */}
        {!credential && (
          <div className="space-y-3">
            <p className="text-xs text-[var(--text-secondary)]">
              Connect this agent to NarraMessenger so it can chat in DMs and groups.
              Paste the bind link from the NarraMessenger app.
            </p>

            <div className="border border-[var(--border-default)] rounded">
              <button
                onClick={() => setSetupOpen((v) => !v)}
                className="w-full flex items-center justify-between px-3 py-2 text-left text-xs hover:bg-[var(--bg-tertiary)] transition-colors"
                aria-expanded={setupOpen}
              >
                <span className="flex items-center gap-2 text-[var(--text-primary)] font-medium">
                  {setupOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  Where do I get the bind link?
                </span>
                <span className="text-[var(--text-secondary)]">~1 min</span>
              </button>
              {setupOpen && (
                <div className="px-3 pb-3 pt-1 space-y-2 text-xs text-[var(--text-secondary)]">
                  <ol className="list-decimal list-inside space-y-1.5 leading-relaxed">
                    <li>Open the <strong className="text-[var(--text-primary)]">NarraMessenger</strong> app and sign in.</li>
                    <li>Go to <strong className="text-[var(--text-primary)]">My Space → My Agents → Bind Agents</strong>.</li>
                    <li>Copy the <strong className="text-[var(--text-primary)]">bind command</strong> shown there (a one-time link).</li>
                    <li>Paste it below and click <strong className="text-[var(--text-primary)]">Bind</strong>.</li>
                  </ol>
                  <div className="text-[var(--text-secondary)] pt-1 border-t border-[var(--border-default)] mt-2">
                    The link contains a one-time token — we use it to bind over Gateway transport and never store it after.
                  </div>
                </div>
              )}
            </div>

            <label className="block">
              <span className="sr-only">Bind command / link</span>
              <Input
                type="text"
                placeholder="Paste the bind link (https://api.netmind.chat/.../setup-guide.md)"
                value={bindCommand}
                onChange={(e) => setBindCommand(e.target.value)}
                className="text-sm"
                autoComplete="off"
                aria-label="Bind command"
              />
            </label>
            <Button
              onClick={handleBind}
              disabled={actionLoading || !bindCommand.trim()}
              className="w-full"
              size="sm"
            >
              {actionLoading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Link className="w-4 h-4 mr-2" />}
              Bind
            </Button>
          </div>
        )}

        {/* State 2: Bound */}
        {credential && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="text-[var(--text-primary)] font-medium">
                  {credential.matrix_user_id || '(agent)'}
                </span>
                <span className="text-[var(--text-secondary)] ml-2">
                  ({credential.connection_mode})
                </span>
              </div>
              <span className="flex items-center gap-1 text-xs text-[var(--color-green-500)]">
                <CheckCircle className="w-3 h-3" aria-hidden="true" /> Connected
              </span>
            </div>
            {credential.owner_matrix_user_id ? (
              <div className="text-xs text-[var(--text-secondary)]">
                Owner: <span className="text-[var(--text-primary)]">{credential.owner_name || credential.owner_matrix_user_id}</span>
              </div>
            ) : (
              <div className="text-xs text-[var(--color-yellow-500)]" role="note">
                ⚠ No owner registered — the agent has no trust signal for NarraMessenger senders.
              </div>
            )}
            <div className="flex gap-2">
              <Button
                onClick={handleUnbind}
                disabled={unbindLoading}
                variant="outline"
                size="sm"
                className="flex-1"
              >
                {unbindLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                ) : (
                  <Unlink className="w-4 h-4 mr-2" />
                )}
                Unbind
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
