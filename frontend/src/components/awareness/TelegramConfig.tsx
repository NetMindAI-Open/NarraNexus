/**
 * TelegramConfig — Per-agent Telegram bot binding configuration.
 *
 * States:
 *   1. No bot bound → show bind form (Bot Token + optional @username)
 *   2. Bot bound   → show connected status (bot @username, owner) + Test/Unbind
 *
 * Simplest of the three IM channels: single token, no OAuth, no manifest.
 * Privacy mode (default ON) is RECOMMENDED — group bots only see
 * @-mentions and /commands, which is the standard Slack/Lark group-bot
 * UX. Disclosure block walks the user through @BotFather setup and
 * explicitly tells them NOT to disable privacy unless they want a
 * passive group listener (rare).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Send,
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
import type { TelegramCredentialData } from '@/types';

import type { ChannelConfigProps } from './IMChannelsSection';

export function TelegramConfig({ onBindStateChange }: ChannelConfigProps = {}) {
  const { agentId } = useConfigStore();

  const [credential, setCredential] = useState<TelegramCredentialData | null>(null);
  const [loading, setLoading] = useState(false);
  // Bind, Test and Unbind are independent — see SlackConfig for the
  // split rationale.
  const [actionLoading, setActionLoading] = useState(false);
  const [testLoading, setTestLoading] = useState(false);
  const [unbindLoading, setUnbindLoading] = useState(false);
  const [error, setError] = useState('');

  const [botToken, setBotToken] = useState('');
  const [ownerUsername, setOwnerUsername] = useState('');
  const [setupOpen, setSetupOpen] = useState(false);
  // Transient green-flash state for Test — see SlackConfig for rationale.
  const [testPassed, setTestPassed] = useState(false);

  const mountedRef = useRef(true);
  const { confirm, dialog: confirmDialog } = useConfirm();

  const fetchCredential = useCallback(async () => {
    if (!agentId) return;
    try {
      setLoading(true);
      setError('');
      const res = await api.getTelegramCredential(agentId);
      if (!mountedRef.current) return;
      if (res.success) {
        setCredential(res.data || null);
      } else {
        setError(res.error || 'Failed to load Telegram credential');
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : 'Failed to fetch Telegram credential');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    setError('');
    setCredential(null);
    setBotToken('');
    setOwnerUsername('');
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
    // Light client-side prefix validation (defensive — backend re-validates)
    if (!/^\d+:[A-Za-z0-9_-]+$/.test(botToken.trim())) {
      setError('Bot token format looks wrong. Expected <digits>:<base64> (e.g. 7981632450:AAH-...).');
      return;
    }
    setActionLoading(true);
    setError('');
    try {
      const res = await api.bindTelegramBot(
        agentId,
        botToken.trim(),
        ownerUsername.trim(),
      );
      if (res.success) {
        setBotToken('');
        setOwnerUsername('');
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

  const handleTest = async () => {
    if (!agentId) return;
    setTestLoading(true);
    setError('');
    try {
      const res = await api.testTelegramConnection(agentId);
      if (!res.success) {
        setError(res.error || 'Telegram test failed');
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
      setError(e instanceof Error ? e.message : 'Test failed');
    } finally {
      if (mountedRef.current) setTestLoading(false);
    }
  };

  const handleUnbind = async () => {
    if (!agentId) return;
    const ok = await confirm({
      title: 'Unbind Telegram bot?',
      message: 'The agent will stop receiving Telegram messages and lose all Telegram tools until you bind again.',
      confirmText: 'Unbind',
      danger: true,
    });
    if (!ok) return;
    setUnbindLoading(true);
    setError('');
    try {
      const res = await api.unbindTelegramBot(agentId);
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
          <Send className="w-4 h-4" />
          Telegram
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
              Bind a Telegram bot to chat with your agent in DMs and groups.
              You'll need a Bot Token from @BotFather.
            </p>

            {/* Disclosure: full @BotFather walkthrough */}
            <div className="border border-[var(--border-default)] rounded">
              <button
                onClick={() => setSetupOpen((v) => !v)}
                className="w-full flex items-center justify-between px-3 py-2 text-left text-xs hover:bg-[var(--bg-tertiary)] transition-colors"
                aria-expanded={setupOpen}
              >
                <span className="flex items-center gap-2 text-[var(--text-primary)] font-medium">
                  {setupOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  How do I get a Bot Token?
                </span>
                <span className="text-[var(--text-secondary)]">~3 min</span>
              </button>
              {setupOpen && (
                <div className="px-3 pb-3 pt-1 space-y-2 text-xs text-[var(--text-secondary)]">
                  <ol className="list-decimal list-inside space-y-1.5 leading-relaxed">
                    <li>
                      Open Telegram, search{' '}
                      <a
                        href="https://t.me/BotFather"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[var(--accent-primary)] hover:underline inline-flex items-center gap-0.5"
                      >
                        @BotFather
                        <ExternalLink className="w-3 h-3" />
                      </a>
                      , start a chat. Send <code className="bg-[var(--bg-tertiary)] px-1">/newbot</code>; pick a display name + username (must end in <code className="bg-[var(--bg-tertiary)] px-1">bot</code>).
                    </li>
                    <li>
                      BotFather replies with a token like <code className="bg-[var(--bg-tertiary)] px-1">7981632450:AAH-kxRP...</code>. Paste it below.
                    </li>
                    <li>
                      <strong className="text-[var(--text-primary)]">Privacy mode — KEEP DEFAULT ON</strong>. By default the bot only sees <code className="bg-[var(--bg-tertiary)] px-1">/commands</code> and @-mentions in groups; this is the right behavior for almost all use cases (saves tokens, prevents spam). DMs are unaffected. Do NOT run <code className="bg-[var(--bg-tertiary)] px-1">/setprivacy → Disable</code> unless you specifically want the bot to see every group message (rare research / note-taking bots only).
                    </li>
                    <li>
                      Optional: send <code className="bg-[var(--bg-tertiary)] px-1">/setjoingroups</code> → bot → <strong>Enable</strong> if you want the bot to be addable to groups.
                    </li>
                    <li>
                      Optional: paste <strong>your</strong> Telegram @username below so the agent recognises owner-vs-stranger DMs. <strong>After binding, send any message to the bot from your @username</strong> — Telegram's API only reveals your numeric user_id on a real DM, so the trust signal activates on first contact (not at bind time).
                    </li>
                  </ol>
                  <div className="text-[var(--text-secondary)] pt-1 border-t border-[var(--border-default)] mt-2">
                    <strong>Group not getting replies?</strong> @-mention the bot. Privacy mode is intentionally on — flipping it would make the bot try to reply to everything in the group.
                  </div>
                </div>
              )}
            </div>

            <div className="space-y-2">
              <label className="block">
                <span className="sr-only">Bot Token</span>
                <Input
                  type="password"
                  placeholder="Bot Token (123456789:AAH-...)"
                  value={botToken}
                  onChange={(e) => setBotToken(e.target.value)}
                  className="text-sm"
                  autoComplete="off"
                  aria-label="Bot Token"
                />
              </label>
              <label className="block">
                <span className="sr-only">Your Telegram @username (optional)</span>
                <Input
                  type="text"
                  placeholder="Your Telegram @username (optional, enables owner trust signal)"
                  value={ownerUsername}
                  onChange={(e) => setOwnerUsername(e.target.value)}
                  className="text-sm"
                  autoComplete="off"
                  aria-label="Owner Telegram @username"
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
              Bind Bot
            </Button>
          </div>
        )}

        {/* State 2: Bot bound */}
        {credential && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="text-[var(--text-primary)] font-medium">
                  @{credential.bot_username || credential.bot_user_id}
                </span>
                <span className="text-[var(--text-secondary)] ml-2">
                  ({credential.bot_user_id})
                </span>
              </div>
              <span className="flex items-center gap-1 text-xs text-[var(--color-green-500)]">
                <CheckCircle className="w-3 h-3" aria-hidden="true" /> Connected
              </span>
            </div>
            {credential.owner_user_id ? (
              <div className="text-xs text-[var(--text-secondary)]">
                Owner: <span className="text-[var(--text-primary)]">{credential.owner_name || `@${credential.owner_username}`}</span>{' '}
                <span className="text-[var(--text-secondary)]">({credential.owner_user_id})</span>
              </div>
            ) : credential.owner_username ? (
              <div className="text-xs text-[var(--color-yellow-500)]" role="note">
                ⏳ Owner registration pending — DM <strong>@{credential.bot_username || 'your bot'}</strong> on Telegram
                from <strong>@{credential.owner_username}</strong> once to activate the trust signal.
                <div className="mt-1 text-[var(--text-secondary)]">
                  (Telegram's API doesn't let bots resolve @usernames at bind time —
                  we wait for the first matching DM, then auto-link.)
                </div>
              </div>
            ) : (
              <div className="text-xs text-[var(--color-yellow-500)]" role="note">
                ⚠ No owner registered — agent has no trust signal for Telegram DMs.
                Re-bind with your @username to enable.
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
                {testPassed ? 'Connected' : 'Test'}
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
                Unbind
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
