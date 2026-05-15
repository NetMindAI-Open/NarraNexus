/**
 * SlackConfig — Per-agent Slack workspace binding configuration.
 *
 * States:
 *   1. No bot bound → show bind form (Bot Token + App-Level Token)
 *   2. Bot bound   → show connected status + Test/Unbind
 *
 * Simpler than LarkConfig because Slack has no OAuth device-flow polling
 * — bot tokens validate synchronously via auth.test on bind submit.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Hash,
  Link,
  Unlink,
  Loader2,
  CheckCircle,
  AlertCircle,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
} from 'lucide-react';

import { Card, CardHeader, CardTitle, CardContent, Button, Input, useConfirm } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import type { SlackCredentialData } from '@/types';

import type { ChannelConfigProps } from './IMChannelsSection';

// Slack App Manifest — paste-and-go YAML for "Create app from manifest".
//
// Single source of truth for the BACKEND copy lives in
// src/xyz_agent_context/module/slack_module/slack_module.py
// (constant SLACK_APP_MANIFEST_YAML). When Slack adds a scope we need,
// update BOTH (the diff is grep-able). Hard-coding here avoids one extra
// network round-trip when the user opens the disclosure.
const SLACK_APP_MANIFEST_YAML = `display_information:
  name: NarraNexus Agent
  description: Your NarraNexus AI agent on Slack
  background_color: "#1a1a1a"
features:
  bot_user:
    display_name: NarraNexus
    always_online: true
  app_home:
    home_tab_enabled: false
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - channels:history
      - channels:read
      - chat:write
      - chat:write.public
      - groups:history
      - groups:read
      - im:history
      - im:read
      - im:write
      - mpim:history
      - mpim:read
      - reactions:read
      - reactions:write
      - users:read
      - users:read.email
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.im
      - message.mpim
  interactivity:
    is_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
`;

export function SlackConfig({ onBindStateChange }: ChannelConfigProps = {}) {
  const { agentId } = useConfigStore();

  const [credential, setCredential] = useState<SlackCredentialData | null>(null);
  const [loading, setLoading] = useState(false);
  // Bind, Test and Unbind are independent actions and shouldn't disable
  // each other. Bind keeps the shared `actionLoading` because the rest
  // of the form is gated on it; Test and Unbind get their own state.
  const [actionLoading, setActionLoading] = useState(false);
  const [testLoading, setTestLoading] = useState(false);
  const [unbindLoading, setUnbindLoading] = useState(false);
  const [error, setError] = useState('');

  const [botToken, setBotToken] = useState('');
  const [appToken, setAppToken] = useState('');
  const [ownerEmail, setOwnerEmail] = useState('');
  const [setupOpen, setSetupOpen] = useState(false);
  const [manifestCopied, setManifestCopied] = useState(false);
  // Transient green-flash state for Test — without this the user has no
  // visual signal that "Test" passed because fetchCredential() usually
  // produces an identical-looking re-render.
  const [testPassed, setTestPassed] = useState(false);

  const handleCopyManifest = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(SLACK_APP_MANIFEST_YAML);
      setManifestCopied(true);
      setTimeout(() => setManifestCopied(false), 2000);
    } catch {
      // Clipboard access denied — surface a hint inline
      setError('Could not copy to clipboard. Select the YAML manually instead.');
    }
  }, []);

  const mountedRef = useRef(true);
  const { confirm, dialog: confirmDialog } = useConfirm();

  const fetchCredential = useCallback(async () => {
    if (!agentId) return;
    try {
      setLoading(true);
      setError('');
      const res = await api.getSlackCredential(agentId);
      if (!mountedRef.current) return;
      if (res.success) {
        setCredential(res.data || null);
      } else {
        setError(res.error || 'Failed to load Slack credential');
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : 'Failed to fetch Slack credential');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    setError('');
    setCredential(null);
    setBotToken('');
    setAppToken('');
    setOwnerEmail('');
    fetchCredential();
  }, [fetchCredential]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const handleBind = async () => {
    if (!agentId || !botToken || !appToken) return;
    setActionLoading(true);
    setError('');
    try {
      const res = await api.bindSlackBot(
        agentId,
        botToken.trim(),
        appToken.trim(),
        ownerEmail.trim(),
      );
      if (res.success) {
        setBotToken('');
        setAppToken('');
        setOwnerEmail('');
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
      const res = await api.testSlackConnection(agentId);
      if (!res.success) {
        setError(res.error || 'Slack test failed');
      } else {
        // Refresh to show latest team/bot info
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
      title: 'Unbind Slack bot?',
      message: 'The agent will stop receiving Slack messages and lose all Slack tools until you bind again.',
      confirmText: 'Unbind',
      danger: true,
    });
    if (!ok) return;
    setUnbindLoading(true);
    setError('');
    try {
      const res = await api.unbindSlackBot(agentId);
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
          <Hash className="w-4 h-4" />
          Slack
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
              Bind a Slack bot to enable messaging in DMs and channels.
              You'll need a Bot Token (<code>xoxb-...</code>) and an App-Level Token
              (<code>xapp-...</code>).
            </p>

            {/* Disclosure: full setup walkthrough */}
            <div className="border border-[var(--border-default)] rounded">
              <button
                onClick={() => setSetupOpen((v) => !v)}
                className="w-full flex items-center justify-between px-3 py-2 text-left text-xs hover:bg-[var(--bg-tertiary)] transition-colors"
                aria-expanded={setupOpen}
              >
                <span className="flex items-center gap-2 text-[var(--text-primary)] font-medium">
                  {setupOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  How do I get these tokens?
                </span>
                <span className="text-[var(--text-secondary)]">~3 min</span>
              </button>
              {setupOpen && (
                <div className="px-3 pb-3 pt-1 space-y-3 text-xs text-[var(--text-secondary)]">
                  <ol className="list-decimal list-inside space-y-2 leading-relaxed">
                    <li>
                      Open{' '}
                      <a
                        href="https://api.slack.com/apps?new_app=1"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[var(--accent-primary)] hover:underline inline-flex items-center gap-0.5"
                      >
                        Slack's app creator
                        <ExternalLink className="w-3 h-3" />
                      </a>
                      , click <strong>Create New App</strong> → <strong>From an app manifest</strong>, pick your workspace.
                    </li>
                    <li>
                      Paste the YAML below into the manifest editor, click{' '}
                      <strong>Next</strong> → <strong>Create</strong>. Slack will pre-configure ~16 OAuth scopes, 5 event subscriptions, Socket Mode, and the bot user.
                    </li>
                    <li>
                      On the app page, click <strong>Install App</strong> →{' '}
                      <strong>Install to Workspace</strong> → <strong>Allow</strong>. Copy the <strong>Bot User OAuth Token</strong> (<code>xoxb-...</code>) into the field below.
                    </li>
                    <li>
                      Click <strong>Basic Information</strong> → scroll to{' '}
                      <strong>App-Level Tokens</strong> → <strong>Generate Token and Scopes</strong>. Add the <code>connections:write</code> scope, click Generate, and copy the token (<code>xapp-...</code>) into the second field below.
                    </li>
                    <li>
                      After binding, invite the bot to any channel where it should listen: <code>/invite @NarraNexus</code>. DMs work automatically.
                    </li>
                  </ol>

                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[var(--text-primary)] font-medium">App Manifest YAML</span>
                      <button
                        onClick={handleCopyManifest}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--border-default)] hover:bg-[var(--bg-tertiary)] transition-colors text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                        title="Copy YAML to clipboard"
                      >
                        {manifestCopied ? (
                          <>
                            <CheckCircle className="w-3 h-3 text-[var(--color-green-500)]" />
                            <span>Copied</span>
                          </>
                        ) : (
                          <>
                            <Copy className="w-3 h-3" />
                            <span>Copy</span>
                          </>
                        )}
                      </button>
                    </div>
                    <pre className="bg-[var(--bg-tertiary)] border border-[var(--border-default)] rounded p-2 text-[10px] font-mono leading-snug overflow-x-auto whitespace-pre max-h-64">
                      <code>{SLACK_APP_MANIFEST_YAML}</code>
                    </pre>
                  </div>
                </div>
              )}
            </div>

            <div className="space-y-2">
              <label className="block">
                <span className="sr-only">Bot Token</span>
                <Input
                  type="password"
                  placeholder="Bot Token (xoxb-...)"
                  value={botToken}
                  onChange={(e) => setBotToken(e.target.value)}
                  className="text-sm"
                  autoComplete="off"
                  aria-label="Bot Token"
                />
              </label>
              <label className="block">
                <span className="sr-only">App-Level Token</span>
                <Input
                  type="password"
                  placeholder="App-Level Token (xapp-...)"
                  value={appToken}
                  onChange={(e) => setAppToken(e.target.value)}
                  className="text-sm"
                  autoComplete="off"
                  aria-label="App-Level Token"
                />
              </label>
              <label className="block">
                <span className="sr-only">Your Slack email (optional)</span>
                <Input
                  type="email"
                  placeholder="Your Slack email (optional, enables owner trust signal)"
                  value={ownerEmail}
                  onChange={(e) => setOwnerEmail(e.target.value)}
                  className="text-sm"
                  autoComplete="off"
                  aria-label="Owner Slack email"
                />
              </label>
            </div>
            <Button
              onClick={handleBind}
              disabled={actionLoading || !botToken || !appToken}
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
                  {credential.team_name || credential.team_id || 'Slack'}
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
                Owner: <span className="text-[var(--text-primary)]">{credential.owner_name || credential.owner_email}</span>{' '}
                <span className="text-[var(--text-secondary)]">({credential.owner_user_id})</span>
              </div>
            ) : (
              <div className="text-xs text-[var(--color-yellow-500)]" role="note">
                ⚠ No owner registered — agent has no trust signal for Slack DMs.
                Re-bind with your Slack email to enable.
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
