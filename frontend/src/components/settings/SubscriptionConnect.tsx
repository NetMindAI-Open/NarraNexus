/**
 * @file_name: SubscriptionConnect.tsx
 * @author: NarraNexus
 * @date: 2026-08-28
 * @description: Claude Code / Codex CLI subscription connect cards.
 *
 * Extracted verbatim from ProviderSettings' add-modal "Sign in" tab so the
 * landing page (SetupPage) can offer subscription sign-in as a first-class
 * peer of the API-key card — the P0 was subscription-only users reading the
 * landing as "API key required" because this surface was buried three
 * layers deep (Advanced → add modal → Sign in tab).
 *
 * Each card surfaces two DECOUPLED state layers (do not conflate them):
 *   1. OS credential state — owned by the CLI (`claude` / `codex`),
 *      probed via /api/providers/claude-status | codex-status. Drives the
 *      Login / Re-login / Logout buttons (claude, Tauri only) and the
 *      terminal hints.
 *   2. Provider record state — owned by NarraNexus (user_providers rows).
 *      Drives "Add as Provider" / "Added ✓" and the setup-token connect.
 *
 * The parent owns the provider list and the POST (addProvider) so its view
 * refreshes on success; this component owns the CLI status lifecycle.
 *
 * Cloud gate lives HERE: the status routes return `allowed: false` for
 * cloud non-staff (the same is_cloud+not-staff predicate that 403s the
 * OAuth card types). This component then renders a one-line explanation
 * (never a silent blank — an empty panel reads as "the page broke"),
 * and callers that own an ENTRY POINT to this surface should hide it via
 * useOauthAllowed (ProviderSettings drops the Sign-in tab). The backend
 * 403 remains the actual security boundary. `allowed` is undefined on
 * local and for cloud staff, so every check MUST be `=== false` — a
 * truthiness check would blank the local mode this component exists to
 * serve.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import type { CliStatusPayload, ProviderRow } from '@/lib/providersApi';
import { useConfigStore } from '@/stores';
import {
  isTauri,
  triggerClaudeLogin,
  triggerClaudeLogout,
  cancelClaudeLogin,
} from '@/lib/tauri';

/** `claude auth login` blocks on a browser OAuth flow; abandoning the tab
 * leaves the CLI on a dead callback server forever. Countdown then SIGTERM. */
const CLAUDE_LOGIN_TIMEOUT_SEC = 600;

function formatCountdown(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

/** Numeric expiries at or above this are epoch MILLISECONDS, below it epoch
 * seconds. The SAME constant as backend/routes/providers.py's
 * EPOCH_MS_THRESHOLD: the backend decides `expired` with it and this file
 * renders the date with it — two values would read a token two ways. */
const EPOCH_MS_THRESHOLD = 1e11;

/** Best-effort render of whatever expiry value the CLI handed us.
 *
 * The Claude Code CLI shifts schemas across minor versions: some builds
 * emit ISO-8601 strings, others emit unix epoch (sec OR ms). We accept
 * any of them. If parsing fails we just show the raw value rather than
 * eating the field — the user still gets *something* useful. */
function formatExpiresAt(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const trimmed = String(raw).trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  let d: Date | null = null;
  if (Number.isFinite(n) && n > 0) {
    d = new Date(n < EPOCH_MS_THRESHOLD ? n * 1000 : n);
  } else {
    const t = Date.parse(trimmed);
    if (!Number.isNaN(t)) d = new Date(t);
  }
  if (!d || Number.isNaN(d.getTime())) return trimmed;
  return d.toLocaleString();
}

type CliStatus = CliStatusPayload;

/** Status dot + identity line + optional expiry — shared by both cards.
 * One shape on purpose: editing the status line means editing it once.
 *
 * Three states, not two: an EXPIRED session is not "never logged in". The
 * backend forces `logged_in` false for it, so a two-state line showed the
 * never-logged-in copy and hid the very details the user needs to fix it
 * (which account, when it expired). Expired keeps the identity and date. */
function CliStatusLine({ status }: { status: CliStatus }) {
  const { t } = useTranslation();
  const expired = status.expired === true;
  const identity = status.email
    ? <> {t('settings.provider.loggedInAs')} <span className="font-mono">{status.email}</span></>
    : null;
  return (
    <div className="flex items-center gap-2 flex-wrap" data-testid="cli-status-line">
      <span className={cn('inline-block w-2 h-2 rounded-full',
        status.logged_in ? 'bg-[var(--color-success)]' :
        status.cli_installed || expired ? 'bg-[var(--color-warning)]' : 'bg-[var(--text-tertiary)]'
      )} />
      <span className="text-sm text-[var(--text-secondary)]">
        {status.logged_in
          ? <>{t('settings.provider.loggedIn')}{identity}</>
          : expired
            ? <>{t('settings.provider.sessionExpired')}{identity}</>
            : status.cli_installed ? t('settings.provider.notLoggedIn') : t('settings.provider.cliNotInstalled')}
      </span>
      {(status.logged_in || expired) && status.expires_at && (
        <span className="text-xs text-[var(--text-tertiary)]">
          {t('settings.provider.expires', { date: formatExpiresAt(status.expires_at) })}
        </span>
      )}
    </div>
  );
}

/** Section B (provider record state), shared by both cards. Labels come
 * in as PRE-TRANSLATED strings — the two cards use different i18n keys
 * (addedAsProvider vs codexAddedAsProvider etc.); building keys by
 * prefix here would silently fall back for codex. */
function ProviderRecordRow({
  added,
  addedLabel,
  loggedIn,
  sessionExpired = false,
  onAdd,
  addLabel,
  addingLabel,
  adding,
  loginHint,
}: {
  added: boolean;
  addedLabel: string;
  loggedIn: boolean;
  /** The record exists but the CLI session behind it has expired: the
   * "✓ added" line alone would contradict the status line above it, so
   * the re-login hint rides underneath. False when the record runs on a
   * transport the CLI session does not back (the setup-token card). */
  sessionExpired?: boolean;
  onAdd: () => void;
  addLabel: string;
  addingLabel: string;
  /** POST in flight: disable + spinner — an unresponsive button reads
   * as frozen (same lesson as the Test buttons). */
  adding: boolean;
  loginHint: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="pt-2 border-t border-[var(--border-subtle)]">
      {added ? (
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-sm text-[var(--color-success)]">
            <span>{'✓'}</span>
            <span>{addedLabel}</span>
          </div>
          {sessionExpired && (
            <p className="text-sm text-[var(--color-warning)]" data-testid="provider-session-expired-hint">
              {t('settings.provider.sessionExpiredHint')}
            </p>
          )}
        </div>
      ) : loggedIn ? (
        <button onClick={onAdd} disabled={adding}
          className="inline-flex items-center px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 transition-colors disabled:opacity-50">
          {adding ? (
            <>
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              {addingLabel}
            </>
          ) : (
            addLabel
          )}
        </button>
      ) : (
        <p className="text-sm text-[var(--text-tertiary)]">{loginHint}</p>
      )}
    </div>
  );
}

/** One card's status probe failed — say so and offer a retry (an
 * eternal "Checking status…" reads as a hang). Retry re-probes BOTH
 * cards (one function, monotonic generation guard). */
function StatusProbeFailedRow({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-3 text-sm text-[var(--text-tertiary)]">
      <span>{t('settings.provider.statusProbeFailed')}</span>
      <button
        type="button"
        onClick={onRetry}
        className="underline underline-offset-2 hover:opacity-80 text-[var(--text-secondary)]"
      >
        {t('settings.provider.retry')}
      </button>
    </div>
  );
}

interface SubscriptionConnectProps {
  /** The user's provider rows — record-state derivation ("Added ✓" vs
   * Add-as-Provider, token-connected) happens HERE, so the subscription
   * source names live in exactly one file. Parents just hand over the
   * list they already hold. */
  providers: ProviderRow[];
  /** Parent-owned POST /api/providers — the parent refreshes its provider
   * list inside, so record state updates flow back down as props.
   * Resolves true on success. */
  addProvider: (body: Record<string, unknown>) => Promise<boolean>;
}

export function SubscriptionConnect({
  providers,
  addProvider,
}: SubscriptionConnectProps) {
  const { t } = useTranslation();
  const userId = useConfigStore((sel) => sel.userId);

  // Tri-state per card: null = probing, 'error' = probe failed (that
  // card shows its own retry line), payload = probed. Per-card on
  // purpose — the two routes fail independently, and an early version
  // only handled "both failed", leaving a single failure stuck on
  // "Checking status…" forever.
  const [claudeStatus, setClaudeStatus] = useState<CliStatus | null | 'error'>(null);
  const [codexStatus, setCodexStatus] = useState<CliStatus | null | 'error'>(null);
  // Which card's Add-as-Provider POST is in flight ('' = none): the
  // buttons must go disabled + show a spinner, same lesson as the Test
  // buttons (an unresponsive button reads as frozen).
  const [adding, setAdding] = useState<'' | 'claude' | 'codex'>('');
  const [claudeLoggingIn, setClaudeLoggingIn] = useState(false);
  const [claudeLoggingOut, setClaudeLoggingOut] = useState(false);
  // Seconds remaining on the login auto-abort timer, or null when no login
  // is in flight (see the countdown effect below).
  const [claudeLoginRemaining, setClaudeLoginRemaining] = useState<number | null>(null);

  // Setup-token paste flow (`claude setup-token` → long-lived subscription
  // token stored server-side, env-injected at spawn; no CLI login state).
  const [setupToken, setSetupToken] = useState('');
  const [savingSetupToken, setSavingSetupToken] = useState(false);

  // Record-state derivation — the ONLY place in the frontend that spells
  // the subscription source names (a third vendor means editing exactly
  // this file). Each card may have its own connected predicate: claude's
  // token transport keys off auth_type, not mere row existence.
  const claudeCard = providers.find((prov) => prov.source === 'claude_oauth') ?? null;
  const hasClaude = claudeCard !== null;
  // Token transport: no CLI login state, no Keychain — see Section C.
  const claudeTokenConnected = claudeCard?.auth_type === 'oauth_token';
  const hasCodex = providers.some((prov) => prov.source === 'codex_oauth');

  // Monotonic call id: a stale response (account switch, slow retry)
  // must never overwrite a newer one. A generation counter instead of an
  // unmount-cancel flag so a MANUAL retry is never self-cancelled.
  const probeGeneration = useRef(0);

  const refreshStatuses = useCallback(async () => {
    const gen = ++probeGeneration.current;
    setClaudeStatus(null);
    setCodexStatus(null);
    const [claudeRes, codexRes] = await Promise.all([
      api.getClaudeStatus().catch(() => null),
      api.getCodexStatus().catch(() => null),
    ]);
    if (gen !== probeGeneration.current) return;
    setClaudeStatus(claudeRes?.success && claudeRes.data ? claudeRes.data : 'error');
    setCodexStatus(codexRes?.success && codexRes.data ? codexRes.data : 'error');
  // userId: switching accounts must re-probe — `allowed` is per-user.
  }, [userId]);

  useEffect(() => {
    refreshStatuses();
  }, [refreshStatuses]);

  // Login auto-abort timer. Set claudeLoginRemaining to N to start
  // counting down to 0; reaching 0 fires cancelClaudeLogin which
  // SIGTERMs the dangling `claude auth login` child on the Rust side.
  // Setting it to null (e.g. on natural completion) clears the timer.
  useEffect(() => {
    if (claudeLoginRemaining === null) return;
    if (claudeLoginRemaining <= 0) {
      cancelClaudeLogin().catch((e) => console.error('cancelClaudeLogin failed:', e));
      // Don't null it here — handleClaudeLogin's finally clears state
      // once the trigger's await resolves with the SIGTERM exit code.
      // Returning early prevents a re-fire next tick.
      return;
    }
    const timer = setTimeout(
      () => setClaudeLoginRemaining((r) => (r === null ? null : r - 1)),
      1000,
    );
    return () => clearTimeout(timer);
  }, [claudeLoginRemaining]);

  const handleAddClaudeOAuth = async () => {
    setAdding('claude');
    try {
      await addProvider({ card_type: 'claude_oauth' });
    } finally {
      setAdding('');
    }
  };

  const handleSaveSetupToken = async () => {
    const token = setupToken.trim();
    if (!token) return;
    setSavingSetupToken(true);
    try {
      // Same card_type; a non-empty api_key makes the backend store/upgrade
      // the card as auth_type=oauth_token (reconnect-in-place keeps slots).
      const ok = await addProvider({ card_type: 'claude_oauth', api_key: token });
      if (ok) setSetupToken('');
    } finally {
      setSavingSetupToken(false);
    }
  };

  const handleAddCodexOAuth = async () => {
    setAdding('codex');
    try {
      await addProvider({ card_type: 'codex_oauth' });
    } finally {
      setAdding('');
    }
  };

  const handleClaudeLogin = async () => {
    setClaudeLoggingIn(true);
    setClaudeLoginRemaining(CLAUDE_LOGIN_TIMEOUT_SEC);
    try {
      await triggerClaudeLogin();
      // After login completes, refresh to pick up the new status
      await refreshStatuses();
    } catch (e) {
      // SIGTERM from the timeout path also lands here (claude exits
      // non-zero). The finally below resets state regardless.
      console.error('Claude login failed:', e);
    } finally {
      setClaudeLoggingIn(false);
      setClaudeLoginRemaining(null);
    }
  };

  const handleClaudeLogout = async () => {
    setClaudeLoggingOut(true);
    try {
      await triggerClaudeLogout();
      await refreshStatuses();
    } catch (e) {
      console.error('Claude logout failed:', e);
    } finally {
      setClaudeLoggingOut(false);
    }
  };

  const claudePayload = typeof claudeStatus === 'object' ? claudeStatus : null;
  const codexPayload = typeof codexStatus === 'object' ? codexStatus : null;

  // Cloud non-staff: the status routes said this caller may not use
  // OAuth cards — explain instead of blanking (see the header comment;
  // `=== false` on purpose, the flag is undefined on local/cloud-staff).
  // This gate stays AHEAD of the failure rows: a successful probe with
  // allowed:false is a verdict, not a failure to retry.
  if (claudePayload?.allowed === false || codexPayload?.allowed === false) {
    return (
      <p
        data-testid="subscription-cloud-managed"
        className="text-sm text-[var(--text-tertiary)]"
      >
        {t('settings.provider.oauthCloudManaged')}
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {/* ---- Claude Code Login Card ---- */}
      <div data-testid="claude-connect-card" className="p-4 rounded-[var(--radius-xl)] border border-[var(--accent-primary)]/20 bg-[var(--accent-primary)]/5">
        <div className="flex items-center gap-2 mb-1">
          <h4 className="text-sm font-medium text-[var(--text-primary)]">
            {t('settings.provider.claudeLoginTitle')}
          </h4>
        </div>
        <p className="text-sm text-[var(--text-tertiary)] mb-3">{t('settings.provider.claudeOauthDesc')}</p>

        {claudeStatus === null && (
          <p className="text-sm text-[var(--text-tertiary)]">{t('settings.provider.checkingStatus')}</p>
        )}
        {claudeStatus === 'error' && (
          <StatusProbeFailedRow onRetry={refreshStatuses} />
        )}

        {claudePayload && (
          <div className="space-y-3">
            {/* ---- Section A: OS credential state ---- */}
            <div className="space-y-2">
              <CliStatusLine status={claudePayload} />

              {/* Action buttons. Always visible when CLI is installed
                * + Tauri — never hidden behind a provider-record check. */}
              {claudePayload.cli_installed && isTauri() && (
                <div className="flex gap-2 flex-wrap">
                  {claudePayload.logged_in ? (
                    <>
                      <button onClick={handleClaudeLogin}
                        disabled={claudeLoggingIn || claudeLoggingOut}
                        className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-50 transition-colors">
                        {claudeLoggingIn
                          ? (claudeLoginRemaining !== null
                              ? t('settings.provider.reLoggingInCountdown', { time: formatCountdown(claudeLoginRemaining) })
                              : t('settings.provider.reLoggingIn'))
                          : t('settings.provider.reLogin')}
                      </button>
                      <button onClick={handleClaudeLogout}
                        disabled={claudeLoggingIn || claudeLoggingOut}
                        className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] border border-[var(--color-error)]/30 text-[var(--color-error)] hover:bg-[var(--color-error)]/5 disabled:opacity-50 transition-colors">
                        {claudeLoggingOut ? t('settings.provider.loggingOut') : t('settings.provider.logout')}
                      </button>
                    </>
                  ) : (
                    <button onClick={handleClaudeLogin}
                      disabled={claudeLoggingIn}
                      className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--accent-primary)] text-[var(--text-inverse)] hover:opacity-90 transition-colors disabled:opacity-50">
                      {claudeLoggingIn
                        ? (claudeLoginRemaining !== null
                            ? t('settings.provider.loggingInCountdown', { time: formatCountdown(claudeLoginRemaining) })
                            : t('settings.provider.loggingIn'))
                        : t('settings.provider.loginWithClaude')}
                    </button>
                  )}
                </div>
              )}

              {/* Web-mode fallback: no Tauri IPC, user goes to terminal. */}
              {!isTauri() && (
                <p className="text-sm text-[var(--text-tertiary)]">
                  {claudePayload.cli_installed
                    ? t('settings.provider.webModeInstalled')
                    : t('settings.provider.webModeNotInstalled')}
                </p>
              )}
              {!claudePayload.cli_installed && isTauri() && (
                <p className="text-sm text-[var(--text-tertiary)]">
                  {t('settings.provider.cliNotInBundle')}
                </p>
              )}
            </div>

            {/* ---- Section B: Provider record state ---- */}
            <ProviderRecordRow
              added={hasClaude}
              addedLabel={t(claudeTokenConnected
                ? 'settings.provider.setupTokenConnected'
                : 'settings.provider.addedAsProvider')}
              loggedIn={claudePayload.logged_in}
              // The setup-token transport bypasses the CLI session, so its
              // expiry says nothing about a token-connected record.
              sessionExpired={claudePayload.expired === true && !claudeTokenConnected}
              onAdd={handleAddClaudeOAuth}
              addLabel={t('settings.provider.addAsProvider')}
              addingLabel={t('settings.provider.addingProvider')}
              adding={adding === 'claude'}
              loginHint={t('settings.provider.loginToAdd')}
            />

            {/* ---- Section C: setup-token connect / replace ----
              *
              * The token transport bypasses the CLI's login state and
              * credential store entirely (the macOS Keychain divergence
              * made staged host credentials unreadable to the runtime
              * CLI — 2026-07-23 incident), so it is the recommended way
              * to connect a subscription. Shown for BOTH states: not
              * connected (recommend) and connected (allow yearly token
              * replacement).
              */}
            <div className="pt-2 border-t border-[var(--border-subtle)] space-y-2">
              <p className="text-sm text-[var(--text-tertiary)]">
                {claudeTokenConnected
                  ? t('settings.provider.setupTokenReplaceHint')
                  : t('settings.provider.setupTokenHint')}
              </p>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={setupToken}
                  onChange={(e) => setSetupToken(e.target.value)}
                  placeholder={t('settings.provider.setupTokenPlaceholder')}
                  className="flex-1 px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)]"
                />
                <button
                  onClick={handleSaveSetupToken}
                  disabled={savingSetupToken || !setupToken.trim()}
                  className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 transition-colors disabled:opacity-50"
                >
                  {savingSetupToken
                    ? t('settings.provider.setupTokenSaving')
                    : t('settings.provider.setupTokenSave')}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ---- Codex CLI Login Card ----
        *
        * Parallel to "Claude Code Login" above. Same two-layer model;
        * login is a terminal action (`codex login` opens a browser); we
        * surface status only — no Tauri IPC for codex yet. Once added as
        * a provider, the Codex OAuth credential becomes assignable to the
        * agent slot.
        */}
      <div data-testid="codex-connect-card" className="p-4 rounded-[var(--radius-xl)] border border-[var(--accent-primary)]/20 bg-[var(--accent-primary)]/5">
        <div className="flex items-center gap-2 mb-1">
          <h4 className="text-sm font-medium text-[var(--text-primary)]">
            {t('settings.provider.codexLoginTitle')}
          </h4>
        </div>
        <p className="text-sm text-[var(--text-tertiary)] mb-3">
          {t('settings.provider.codexOauthDesc')}
        </p>

        {codexStatus === null && (
          <p className="text-sm text-[var(--text-tertiary)]">
            {t('settings.provider.checkingStatus')}
          </p>
        )}
        {codexStatus === 'error' && (
          <StatusProbeFailedRow onRetry={refreshStatuses} />
        )}

        {codexPayload && (
          <div className="space-y-3">
            {/* ---- Section A: OS credential state ---- */}
            <div className="space-y-2">
              <CliStatusLine status={codexPayload} />

              {/* Always show terminal hint. Codex CLI's OAuth flow
                * opens a browser when `codex login` runs; we don't
                * shell out via Tauri yet (unlike claude). */}
              <p className="text-sm text-[var(--text-tertiary)]">
                {codexPayload.cli_installed
                  ? t('settings.provider.codexTerminalHint')
                  : t('settings.provider.codexInstallHint')}
              </p>
            </div>

            {/* ---- Section B: Provider record state ---- */}
            <ProviderRecordRow
              added={hasCodex}
              addedLabel={t('settings.provider.codexAddedAsProvider')}
              loggedIn={codexPayload.logged_in}
              sessionExpired={codexPayload.expired === true}
              onAdd={handleAddCodexOAuth}
              addLabel={t('settings.provider.addAsProvider')}
              addingLabel={t('settings.provider.addingProvider')}
              adding={adding === 'codex'}
              loginHint={t('settings.provider.codexLoginToAdd')}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export default SubscriptionConnect;
