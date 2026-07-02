/**
 * DiscordConfig — Per-agent Discord bot binding configuration.
 *
 * States:
 *   1. No bot bound → bind form (Bot Token + optional numeric owner user ID)
 *   2. Bot bound   → connected status (bot name, owner) + Test/Unbind
 *
 * Single token from the Developer Portal, no OAuth dance at bind time. The
 * one load-bearing manual step is the **Message Content Intent** — without
 * it Discord delivers empty message bodies, so the disclosure block calls
 * it out prominently.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation, Trans } from 'react-i18next';
import {
  Bot,
  Link,
  Unlink,
  Loader2,
  CheckCircle,
  AlertCircle,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  ExternalLink,
} from 'lucide-react';

import { Card, CardHeader, CardTitle, CardContent, Button, Input, useConfirm } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import type { DiscordCredentialData } from '@/types';

import type { ChannelConfigProps } from './IMChannelsSection';

export function DiscordConfig({ onBindStateChange }: ChannelConfigProps = {}) {
  const { t } = useTranslation();
  const { agentId } = useConfigStore();

  const [credential, setCredential] = useState<DiscordCredentialData | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [testLoading, setTestLoading] = useState(false);
  const [unbindLoading, setUnbindLoading] = useState(false);
  const [error, setError] = useState('');

  const [botToken, setBotToken] = useState('');
  const [ownerUserId, setOwnerUserId] = useState('');
  const [setupOpen, setSetupOpen] = useState(false);
  const [testPassed, setTestPassed] = useState(false);

  const mountedRef = useRef(true);
  const { confirm, dialog: confirmDialog } = useConfirm();

  const fetchCredential = useCallback(async () => {
    if (!agentId) return;
    try {
      setLoading(true);
      setError('');
      const res = await api.getDiscordCredential(agentId);
      if (!mountedRef.current) return;
      if (res.success) {
        setCredential(res.data || null);
      } else {
        setError(res.error || t('awareness.discord.errLoad'));
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : t('awareness.discord.errFetch'));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [agentId, t]);

  useEffect(() => {
    setError('');
    setCredential(null);
    setBotToken('');
    setOwnerUserId('');
    fetchCredential();
  }, [fetchCredential]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const handleBind = async () => {
    if (!agentId || !botToken) return;
    if (ownerUserId.trim() && !/^\d+$/.test(ownerUserId.trim())) {
      setError(t('awareness.discord.errOwnerIdNumeric'));
      return;
    }
    setActionLoading(true);
    setError('');
    try {
      const res = await api.bindDiscordBot(agentId, botToken.trim(), ownerUserId.trim());
      if (res.success) {
        setBotToken('');
        setOwnerUserId('');
        await fetchCredential();
        onBindStateChange?.();
      } else {
        setError(res.error || t('awareness.discord.errBind'));
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('awareness.discord.errBind'));
    } finally {
      if (mountedRef.current) setActionLoading(false);
    }
  };

  const handleTest = async () => {
    if (!agentId) return;
    setTestLoading(true);
    setError('');
    try {
      const res = await api.testDiscordConnection(agentId);
      if (!res.success) {
        setError(res.error || t('awareness.discord.errTest'));
      } else {
        await fetchCredential();
        onBindStateChange?.();
        if (mountedRef.current) {
          setTestPassed(true);
          setTimeout(() => {
            if (mountedRef.current) setTestPassed(false);
          }, 2000);
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('awareness.discord.errTestGeneric'));
    } finally {
      if (mountedRef.current) setTestLoading(false);
    }
  };

  const handleUnbind = async () => {
    if (!agentId) return;
    const ok = await confirm({
      title: t('awareness.discord.unbindConfirmTitle'),
      message: t('awareness.discord.unbindConfirmMessage'),
      confirmText: t('awareness.common.unbind'),
      danger: true,
    });
    if (!ok) return;
    setUnbindLoading(true);
    setError('');
    try {
      const res = await api.unbindDiscordBot(agentId);
      if (res.success) {
        await fetchCredential();
        onBindStateChange?.();
      } else {
        setError(res.error || t('awareness.discord.errUnbind'));
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('awareness.discord.errUnbind'));
    } finally {
      if (mountedRef.current) setUnbindLoading(false);
    }
  };

  return (
    <Card>
      {confirmDialog}
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="w-4 h-4" />
          Discord
        </CardTitle>
        <button
          onClick={() => fetchCredential()}
          disabled={loading}
          className="p-1 rounded hover:bg-[var(--bg-tertiary)] transition-colors text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          title={t('awareness.common.refresh')}
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
              {t('awareness.discord.intro')}
            </p>

            <div className="border border-[var(--border-default)] rounded">
              <button
                onClick={() => setSetupOpen((v) => !v)}
                className="w-full flex items-center justify-between px-3 py-2 text-left text-xs hover:bg-[var(--bg-tertiary)] transition-colors"
                aria-expanded={setupOpen}
              >
                <span className="flex items-center gap-2 text-[var(--text-primary)] font-medium">
                  {setupOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  {t('awareness.discord.setupQuestion')}
                </span>
                <span className="text-[var(--text-secondary)]">{t('awareness.common.threeMin')}</span>
              </button>
              {setupOpen && (
                <div className="px-3 pb-3 pt-1 space-y-2 text-xs text-[var(--text-secondary)]">
                  <ol className="list-decimal list-inside space-y-1.5 leading-relaxed">
                    <li>
                      <Trans i18nKey="awareness.discord.step1">
                        Open the{' '}
                        <a
                          href="https://discord.com/developers/applications"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[var(--accent-primary)] hover:underline inline-flex items-center gap-0.5"
                        >
                          Developer Portal
                          <ExternalLink className="w-3 h-3" />
                        </a>
                        , click <strong>New Application</strong>, then open the <strong>Bot</strong> tab.
                      </Trans>
                    </li>
                    <li>
                      <Trans i18nKey="awareness.discord.step2">
                        <strong className="text-[var(--color-yellow-500)]">REQUIRED:</strong> on the Bot page, under
                        {' '}<strong>Privileged Gateway Intents</strong>, turn ON <strong>Message Content Intent</strong>.
                        Without it the bot receives messages with an empty body and can't read what people say.
                      </Trans>
                    </li>
                    <li>
                      <Trans i18nKey="awareness.discord.step3">
                        Click <strong>Reset Token</strong> → <strong>Copy</strong>. Paste it below. Keep it secret.
                      </Trans>
                    </li>
                    <li>
                      <Trans i18nKey="awareness.discord.step4">
                        Open <strong>OAuth2 → URL Generator</strong>, tick scope <code className="bg-[var(--bg-tertiary)] px-1">bot</code> and
                        permissions <em>View Channels</em>, <em>Send Messages</em>, <em>Read Message History</em>; open the
                        generated URL and invite the bot to your server.
                      </Trans>
                    </li>
                    <li>
                      <Trans i18nKey="awareness.discord.step5">
                        Optional: paste <strong>your</strong> numeric Discord user ID below so the agent recognises
                        owner-vs-stranger. Enable Discord → Settings → Advanced → <strong>Developer Mode</strong>, then
                        right-click your name → <strong>Copy User ID</strong>.
                      </Trans>
                    </li>
                  </ol>
                  <div className="text-[var(--text-secondary)] pt-1 border-t border-[var(--border-default)] mt-2">
                    <Trans i18nKey="awareness.discord.troubleBlank">
                      <strong>Bot sees blank messages?</strong> Message Content Intent is off — re-enable it on the Bot page.
                    </Trans>
                    <br />
                    <Trans i18nKey="awareness.discord.troubleNoReply">
                      <strong>No replies in a server?</strong> @-mention the bot; it stays silent in channels until addressed.
                    </Trans>
                  </div>
                </div>
              )}
            </div>

            <div className="space-y-2">
              <label className="block">
                <span className="sr-only">{t('awareness.discord.botToken')}</span>
                <Input
                  type="password"
                  placeholder={t('awareness.discord.botToken')}
                  value={botToken}
                  onChange={(e) => setBotToken(e.target.value)}
                  className="text-sm"
                  autoComplete="off"
                  aria-label={t('awareness.discord.botToken')}
                />
              </label>
              <label className="block">
                <span className="sr-only">{t('awareness.discord.ownerIdLabel')}</span>
                <Input
                  type="text"
                  placeholder={t('awareness.discord.ownerIdPlaceholder')}
                  value={ownerUserId}
                  onChange={(e) => setOwnerUserId(e.target.value)}
                  className="text-sm"
                  autoComplete="off"
                  aria-label={t('awareness.discord.ownerIdAria')}
                />
              </label>
            </div>
            <Button
              onClick={handleBind}
              disabled={actionLoading || !botToken}
              className="w-full"
              size="sm"
            >
              {actionLoading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Link className="w-4 h-4 mr-2" />}
              {t('awareness.common.bindBot')}
            </Button>
          </div>
        )}

        {/* State 2: Bot bound */}
        {credential && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="text-[var(--text-primary)] font-medium">
                  {credential.bot_username || credential.bot_user_id}
                </span>
                <span className="text-[var(--text-secondary)] ml-2">
                  ({credential.bot_user_id})
                </span>
              </div>
              <span className="flex items-center gap-1 text-xs text-[var(--color-green-500)]">
                <CheckCircle className="w-3 h-3" aria-hidden="true" /> {t('awareness.common.connected')}
              </span>
            </div>
            {credential.owner_user_id ? (
              <div className="text-xs text-[var(--text-secondary)]">
                {t('awareness.common.owner')}: <span className="text-[var(--text-primary)]">{credential.owner_name || credential.owner_user_id}</span>{' '}
                <span className="text-[var(--text-secondary)]">({credential.owner_user_id})</span>
              </div>
            ) : (
              <div className="text-xs text-[var(--color-yellow-500)]" role="note">
                {t('awareness.discord.noOwner')}
              </div>
            )}
            <div className="flex gap-2">
              <Button
                onClick={handleTest}
                disabled={testLoading}
                variant="outline"
                size="sm"
                className="flex-1"
              >
                {testLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                ) : testPassed ? (
                  <CheckCircle className="w-4 h-4 mr-2 text-[var(--color-green-500)]" />
                ) : null}
                {testPassed ? t('awareness.common.connected') : t('awareness.common.test')}
              </Button>
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
                {t('awareness.common.unbind')}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
