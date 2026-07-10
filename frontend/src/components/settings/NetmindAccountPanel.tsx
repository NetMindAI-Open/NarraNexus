/**
 * @file NetmindAccountPanel.tsx
 * @author NetMind.AI
 * @date 2026-07-02
 * @description NetMind account & subscription panel.
 *
 * Cloud-web only. Reads GET /api/billing/subscription and renders one of four
 * states (S0 hidden in local mode, S1 Free, S2 Pro active, S3 cancelled but
 * still in-period), plus the sandbox free-tier notice (module G).
 *
 * Phase 3 adds subscription actions (module C/D): subscribe → Stripe checkout →
 * poll /me until ACTIVE; cancel (confirm) → auto-renew off; reactivate (resume
 * auto-renew). Payment return has no deterministic desktop signal, so we also
 * refresh on window focus + poll with a bounded window (C3 mitigation).
 *
 * Phase 4 adds top-up (module E): preset tiers (+ custom amount) → hosted
 * Stripe checkout (checkout_url) → openExternal → poll by-session until
 * succeeded/failed → refresh balance. Same bounded-poll + on-focus pattern.
 *
 * Mirrors QuotaPanel: cloud-mode gate + `return null` when not applicable.
 * Copy is fully i18n-keyed (settings.netmind.*) with English source defaults.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/api';
import { platform } from '@/lib/platform';
import { Button } from '@/components/ui';
import type { FeeInfo, FinanceRecord, SubscriptionMe } from '@/types';
import { useRuntimeStore } from '@/stores/runtimeStore';

type PanelState = 'loading' | 'error' | 'free' | 'pro_active' | 'pro_cancelled';

const POLL_INTERVAL_MS = 4000;
const POLL_MAX_MS = 180000; // 3 min bound — never poll forever

// Preset top-up tiers (USD). The API accepts any positive amount; these are a
// NarraNexus-side convenience (module E / D-5). A custom amount overrides them.
const RECHARGE_TIERS = [5, 10, 20, 50];

type RechargeState = 'idle' | 'processing' | 'success' | 'failed';

function resolveState(me: SubscriptionMe | null): PanelState {
  if (!me) return 'error';
  const sub = me.subscription;
  if (!sub) return 'free'; // S1
  if (sub.status === 'ACTIVE' && sub.auto_renew) return 'pro_active'; // S2
  if (sub.status === 'ACTIVE' && !sub.auto_renew) return 'pro_cancelled'; // S3
  return 'free';
}

function formatDate(unixSeconds: number): string {
  try {
    return new Date(unixSeconds * 1000).toISOString().slice(0, 10);
  } catch {
    return '—';
  }
}

function errMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function NetmindAccountPanel() {
  const { t } = useTranslation();
  const mode = useRuntimeStore((s) => s.mode);
  const isCloud = mode === 'cloud-web';
  const [me, setMe] = useState<SubscriptionMe | null>(null);
  const [state, setState] = useState<PanelState>('loading');
  const [busy, setBusy] = useState(false); // an action is in flight
  const [polling, setPolling] = useState(false); // awaiting payment return
  const [actionError, setActionError] = useState<string | null>(null);
  const [fee, setFee] = useState<FeeInfo | null>(null);
  const [feeLoaded, setFeeLoaded] = useState(false);
  const [records, setRecords] = useState<FinanceRecord[]>([]);
  const [useResult, setUseResult] = useState<{ ok: boolean; msg: string } | null>(null);
  // Top-up (module E): selected preset tier + optional custom amount override.
  const [tier, setTier] = useState<number>(10);
  const [custom, setCustom] = useState<string>('');
  const [rechargeState, setRechargeState] = useState<RechargeState>('idle');
  const [rechargeError, setRechargeError] = useState<string | null>(null);
  const [showActivity, setShowActivity] = useState(false); // recent activity collapsed by default
  const mounted = useRef(true);
  // Synchronous locks: React state (busy/polling) updates are async/batched, so
  // a fast double-click can fire a handler twice before `disabled` re-renders.
  // Refs flip synchronously and are the real guard against duplicate
  // subscribe → duplicate Stripe checkout sessions.
  const busyRef = useRef(false);
  const pollingRef = useRef(false);
  const rechargeRef = useRef(false); // synchronous double-submit guard
  // Identifies the active top-up attempt. Bumping it invalidates any in-flight
  // poll loop (used to stop waiting / supersede) so a stale loop can never
  // overwrite the UI or block a fresh attempt.
  const rechargeGenRef = useRef(0);

  const load = useCallback(async () => {
    // Fetch subscription + balance concurrently; each result is handled
    // independently so a fee-info failure never blanks the subscription status
    // (and vice versa). Note: only a FETCH failure is isolated here — the
    // balance render itself must stay null-safe against a partial 200 payload.
    const [subR, feeR, recR] = await Promise.allSettled([
      api.getSubscription(),
      api.getFeeInfo(),
      api.getRecords(),
    ]);
    if (!mounted.current) return;
    if (subR.status === 'fulfilled') {
      const data = subR.value.data ?? null;
      setMe(data);
      setState(resolveState(data));
    } else {
      setState('error');
    }
    setFee(feeR.status === 'fulfilled' ? feeR.value.data ?? null : null);
    setFeeLoaded(true);
    setRecords(recR.status === 'fulfilled' ? recR.value.data ?? [] : []);
  }, []);

  useEffect(() => {
    mounted.current = true;
    if (isCloud) void load();
    return () => {
      mounted.current = false;
    };
  }, [isCloud, load]);

  // C3 mitigation: no deterministic signal when the user returns from the
  // external Stripe window (esp. desktop). Refresh whenever the tab regains
  // focus so a completed payment reflects without a manual reload.
  useEffect(() => {
    if (!isCloud) return;
    const onFocus = () => void load();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [isCloud, load]);

  // Poll /me until the subscription flips to ACTIVE (bounded), used after
  // subscribe kicks off an external payment.
  const pollUntilActive = useCallback(async () => {
    if (pollingRef.current) return; // never run two overlapping poll loops
    pollingRef.current = true;
    setPolling(true);
    const deadline = Date.now() + POLL_MAX_MS;
    let becameActive = false;
    try {
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        if (!mounted.current) return;
        try {
          const r = await api.getSubscription();
          const data = r.data ?? null;
          if (data?.subscription?.status === 'ACTIVE') {
            setMe(data);
            setState(resolveState(data));
            becameActive = true;
            return;
          }
        } catch {
          /* transient — keep polling until the deadline */
        }
      }
      // Deadline hit without seeing ACTIVE — don't vanish silently.
      if (mounted.current && !becameActive) {
        setActionError(
          t('settings.netmind.pollTimeout',
            "Still not active. If you completed payment, refresh in a moment."),
        );
      }
    } finally {
      pollingRef.current = false;
      if (mounted.current) setPolling(false);
    }
  }, [t]);

  const handleSubscribe = useCallback(async () => {
    if (busyRef.current) return; // synchronous double-click guard
    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    try {
      const r = await api.subscribe();
      const url = r.data?.checkout_url;
      if (!url) throw new Error('No checkout URL returned');
      await platform.openExternal(url);
      void pollUntilActive(); // reflect the result when payment completes
    } catch (e) {
      if (mounted.current) setActionError(errMessage(e));
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [pollUntilActive]);

  const handleCancel = useCallback(async () => {
    if (busyRef.current) return;
    if (!window.confirm(t('settings.netmind.cancelConfirm',
      'Cancel = turn off auto-renew. You stay on Pro until the period ends — no immediate downgrade, no prorated refund. Continue?'))) {
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    try {
      await api.cancelSubscription();
      await load();
    } catch (e) {
      if (mounted.current) setActionError(errMessage(e));
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [t, load]);

  const handleReactivate = useCallback(async () => {
    if (busyRef.current) return;
    // reactivate re-enables auto-renew (may trigger a charge) — confirm, since
    // its exact billing semantics are still pending NetMind confirmation.
    if (!window.confirm(t('settings.netmind.reactivateConfirm',
      'Resume auto-renew for your Pro subscription?'))) {
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    try {
      await api.reactivateSubscription();
      await load();
    } catch (e) {
      if (mounted.current) setActionError(errMessage(e));
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [t, load]);

  const handleUseSubscription = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setUseResult(null);
    try {
      await api.useSubscription();
      if (mounted.current) {
        setUseResult({
          ok: true,
          msg: t('settings.netmind.useSubscribeOk',
            'Connected — you can now pick a model in LLM Providers.'),
        });
      }
    } catch (e) {
      if (mounted.current) setUseResult({ ok: false, msg: errMessage(e) });
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [t]);

  // Poll a recharge by Stripe session id until succeeded/failed (bounded). On
  // success, reload so the balance hero + activity reflect the new credit.
  // `gen` tags this loop; if rechargeGenRef moves on (user stopped waiting or
  // started another top-up) the loop bails without touching the UI.
  const pollRechargeStatus = useCallback(async (sessionId: string, gen: number) => {
    const deadline = Date.now() + POLL_MAX_MS;
    const current = () => mounted.current && rechargeGenRef.current === gen;
    try {
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        if (!current()) return; // unmounted / cancelled / superseded
        try {
          const r = await api.rechargeStatus(sessionId);
          if (!current()) return; // re-check after the await
          const st = r.data?.status;
          if (st === 'succeeded') {
            await load(); // refresh balance + activity
            if (current()) setRechargeState('success');
            return;
          }
          if (st === 'failed') {
            if (current()) {
              setRechargeState('failed');
              setRechargeError(
                t('settings.netmind.rechargeFailed', 'Payment failed or was cancelled.'),
              );
            }
            return;
          }
        } catch {
          /* transient — keep polling until the deadline */
        }
      }
      if (current()) {
        // Deadline hit without a terminal status — don't claim success.
        setRechargeState('failed');
        setRechargeError(
          t('settings.netmind.pollTimeout',
            'Still not active. If you completed payment, refresh in a moment.'),
        );
      }
    } finally {
      // Only release the submit guard if we're still the active attempt.
      if (rechargeGenRef.current === gen) rechargeRef.current = false;
    }
  }, [load, t]);

  const handleRecharge = useCallback(async () => {
    if (rechargeRef.current) return; // synchronous double-submit guard
    // Number() (not parseFloat) so "5abc" → NaN is rejected, not silently 5.
    const raw = custom.trim();
    const amount = raw ? Number(raw) : tier;
    if (!Number.isFinite(amount) || amount <= 0) {
      setRechargeState('failed');
      setRechargeError(
        t('settings.netmind.rechargeInvalidAmount', 'Enter an amount greater than 0.'),
      );
      return;
    }
    rechargeRef.current = true;
    const gen = ++rechargeGenRef.current; // this attempt owns the poll
    setRechargeState('processing');
    setRechargeError(null);
    try {
      const r = await api.recharge(amount);
      const url = r.data?.checkout_url;
      const sid = r.data?.session_id;
      if (!url || !sid) throw new Error('No checkout URL returned');
      await platform.openExternal(url);
      void pollRechargeStatus(sid, gen); // reflect the result when payment completes
    } catch (e) {
      if (mounted.current && rechargeGenRef.current === gen) {
        setRechargeState('failed');
        setRechargeError(errMessage(e));
      }
      rechargeRef.current = false;
    }
  }, [custom, tier, t, pollRechargeStatus]);

  // User closed the payment window / doesn't want to keep waiting: invalidate
  // the in-flight poll (bump the generation) and return to idle so they can
  // retry immediately. If they DID pay, the on-focus reload + activity list
  // still surface it; this only stops the blocking "waiting" state.
  const handleStopWaitingRecharge = useCallback(() => {
    rechargeGenRef.current += 1; // the running poll loop will bail on next tick
    rechargeRef.current = false;
    setRechargeState('idle');
    setRechargeError(null);
  }, []);

  if (!isCloud) return null; // S0

  // Activity shows settled entries only — drop `pending` (abandoned checkouts
  // linger as pending until the Stripe session expires ~24h later).
  const settledRecords = records.filter(
    (r) => (r.status || '').toLowerCase() !== 'pending',
  );

  // Plan badge (top-right): reflects the NetMind.AI Power plan state.
  const planBadge = (() => {
    if (state === 'pro_active') {
      return (
        <span className="shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full bg-[var(--accent-primary)]/12 text-[var(--accent-primary)]">
          {t('settings.netmind.planPro', 'Pro')}
        </span>
      );
    }
    if (state === 'pro_cancelled') {
      return (
        <span className="shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full bg-[var(--color-warning)]/12 text-[var(--color-warning)]">
          {t('settings.netmind.badgeCancelled', 'Pro · ending')}
        </span>
      );
    }
    if (state === 'free') {
      return (
        <span className="shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full bg-[var(--bg-sunken)] text-[var(--text-tertiary)]">
          {t('settings.netmind.badgeFree', 'Free')}
        </span>
      );
    }
    return null;
  })();

  // Primary action + status line, driven by the current plan state.
  const actionButton = () => {
    if (state === 'free') {
      return (
        <Button variant="accent" size="sm" onClick={handleSubscribe} disabled={busy || polling}>
          {busy ? t('settings.netmind.working', 'Working…') : t('settings.netmind.subscribeBtn', 'Subscribe to Pro')}
        </Button>
      );
    }
    if (state === 'pro_active') {
      return (
        <Button variant="outline" size="sm" onClick={handleCancel} disabled={busy}>
          {busy ? t('settings.netmind.working', 'Working…') : t('settings.netmind.cancelBtn', 'Cancel subscription')}
        </Button>
      );
    }
    if (state === 'pro_cancelled') {
      return (
        <Button variant="accent" size="sm" onClick={handleReactivate} disabled={busy}>
          {busy ? t('settings.netmind.working', 'Working…') : t('settings.netmind.reactivateBtn', 'Resume auto-renew')}
        </Button>
      );
    }
    return null;
  };

  const statusLine = () => {
    if (state === 'free') return t('settings.netmind.free', 'Current plan: Free (not subscribed)');
    if (state === 'pro_active' && me?.subscription) {
      return `${t('settings.netmind.proActive', 'Pro · active')} · ${t('settings.netmind.validUntil', 'valid until')} ${formatDate(me.subscription.current_period_end)}`;
    }
    if (state === 'pro_cancelled' && me?.subscription) {
      return t('settings.netmind.expiresDowngrade', 'Valid until {{date}}, then downgrades to Free', {
        date: formatDate(me.subscription.current_period_end),
      });
    }
    return '';
  };

  return (
    <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] overflow-hidden">
      {/* Header — product brand + plan badge */}
      <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-[var(--border-subtle)]">
        <div>
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">NetMind.AI Power</h3>
          <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
            {t('settings.netmind.subtitle', 'Power plan & credits · used for your LLM API usage')}
          </p>
        </div>
        {planBadge}
      </div>

      {state === 'loading' && (
        <p className="px-4 py-4 text-sm text-[var(--text-secondary)]">
          {t('settings.netmind.loading', 'Loading…')}
        </p>
      )}
      {state === 'error' && (
        <p className="px-4 py-4 text-sm text-[var(--color-error)]">
          {t('settings.netmind.error',
            'Could not load your NetMind.AI Power account. If your login expired, sign in again and refresh.')}
        </p>
      )}

      {state !== 'loading' && state !== 'error' && (
        <div className="px-4 py-4 space-y-4">
          {/* Balance hero */}
          {feeLoaded && fee && (
            <div>
              <div className="text-2xl font-semibold font-mono text-[var(--text-primary)] leading-none">
                ${fee.metrics?.free_credit ?? '—'}
              </div>
              <div className="mt-1 text-xs text-[var(--text-tertiary)]">
                {t('settings.netmind.currentBalance', 'Current balance')}
                {' · '}
                {t('settings.netmind.monthlyGrant', 'Monthly grant')} ${fee.metrics?.monthly_free_credit ?? '—'}
              </div>
              {fee.eligible === false && (
                <div className="mt-1.5 text-xs text-[var(--color-warning)]">
                  {t('settings.netmind.notEligible',
                    'Cannot incur paid usage right now (no balance / not eligible).')}
                </div>
              )}
              {fee.checks?.has_arrears && (
                <div className="mt-1 text-xs text-[var(--color-error)]">
                  {t('settings.netmind.hasArrears', 'You have outstanding arrears.')}
                </div>
              )}
            </div>
          )}

          {/* Top-up (module E) — any Free/Pro user can add credits */}
          <div className="space-y-2">
            <div className="text-sm font-medium text-[var(--text-primary)]">
              {t('settings.netmind.rechargeTitle', 'Add credits')}
            </div>
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.rechargeDesc',
                'Top up your NetMind.AI Power balance. Credits are kept regardless of plan.')}
            </p>
            <div className="flex flex-wrap items-center gap-1.5">
              {RECHARGE_TIERS.map((v) => {
                const active = !custom.trim() && tier === v;
                return (
                  <button
                    key={v}
                    type="button"
                    onClick={() => { setTier(v); setCustom(''); }}
                    disabled={rechargeState === 'processing'}
                    className={`px-3 py-1 rounded-md text-sm border transition-colors disabled:opacity-50 ${
                      active
                        ? 'border-[var(--accent-primary)] text-[var(--accent-primary)] bg-[var(--accent-primary)]/8'
                        : 'border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--border-strong)]'
                    }`}
                  >
                    ${v}
                  </button>
                );
              })}
              <div className="flex items-center gap-1 ml-1">
                <span className="text-sm text-[var(--text-tertiary)]">$</span>
                <input
                  type="number"
                  min="1"
                  inputMode="decimal"
                  value={custom}
                  onChange={(e) => setCustom(e.target.value)}
                  placeholder={t('settings.netmind.rechargeCustom', 'Custom')}
                  disabled={rechargeState === 'processing'}
                  className="w-24 px-2 py-1 rounded-md text-sm bg-[var(--bg-primary)] border border-[var(--border-default)] text-[var(--text-primary)] disabled:opacity-50"
                />
              </div>
              <Button
                variant="accent"
                size="sm"
                onClick={handleRecharge}
                disabled={rechargeState === 'processing'}
              >
                {rechargeState === 'processing'
                  ? t('settings.netmind.working', 'Working…')
                  : t('settings.netmind.rechargeBtn', 'Recharge')}
              </Button>
            </div>
            {rechargeState === 'processing' && (
              <div className="flex items-start justify-between gap-3">
                <p className="text-xs text-[var(--text-tertiary)] flex-1">
                  {t('settings.netmind.rechargeProcessing',
                    'Waiting for payment… complete it in the opened window; your balance updates automatically.')}
                </p>
                <button
                  type="button"
                  onClick={handleStopWaitingRecharge}
                  className="shrink-0 text-xs text-[var(--text-secondary)] underline underline-offset-2 hover:text-[var(--text-primary)]"
                >
                  {t('settings.netmind.rechargeStopWaiting', 'Stop waiting')}
                </button>
              </div>
            )}
            {rechargeState === 'success' && (
              <p className="text-xs text-[var(--color-success)]">
                {t('settings.netmind.rechargeSuccess', 'Top-up complete — balance updated.')}
              </p>
            )}
            {rechargeState === 'failed' && rechargeError && (
              <p className="text-xs text-[var(--color-error)]">{rechargeError}</p>
            )}
          </div>

          {/* Plan status + primary action */}
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-[var(--text-secondary)]">{statusLine()}</p>
            {actionButton()}
          </div>
          {polling && (
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.awaitingPayment',
                'Waiting for payment to complete… this panel refreshes automatically. If you already paid, come back to this tab.')}
            </p>
          )}
          {actionError && <p className="text-xs text-[var(--color-error)]">{actionError}</p>}

          {/* Use this NetMind.AI Power account to power the agent */}
          <div className="rounded-md bg-[var(--bg-sunken)] p-3 space-y-2">
            <div className="text-sm font-medium text-[var(--text-primary)]">
              {t('settings.netmind.useTitle', 'Power NarraNexus with this account')}
            </div>
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.useDesc',
                'Run agent conversations on your NetMind.AI Power credits — no API key to paste.')}
            </p>
            <Button variant="accent" size="sm" onClick={handleUseSubscription} disabled={busy}>
              {busy ? t('settings.netmind.working', 'Working…') : t('settings.netmind.useSubscribeBtn', 'Use this account')}
            </Button>
            {useResult && (
              <p className={`text-xs ${useResult.ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]'}`}>
                {useResult.msg}
              </p>
            )}
          </div>

          {/* Recent activity — collapsed by default (keeps the panel clean),
              settled ledger only. `pending` rows are hidden: every abandoned
              checkout (opened, not paid) leaves a pending record that only flips
              to failed ~24h later when the Stripe session expires, so showing
              them just piles up noise. In-progress payment is already surfaced
              by the live "waiting" state above. */}
          {settledRecords.length > 0 && (
            <div className="pt-3 border-t border-[var(--border-subtle)]">
              <button
                type="button"
                onClick={() => setShowActivity((v) => !v)}
                className="flex items-center gap-1 text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                aria-expanded={showActivity}
              >
                <span className={`transition-transform ${showActivity ? 'rotate-90' : ''}`}>›</span>
                {t('settings.netmind.activityTitle', 'Recent activity')}
              </button>
              {showActivity && (
              <ul className="mt-1.5 space-y-1">
                {settledRecords.slice(0, 8).map((r) => {
                  const income = r.direction === 'income';
                  return (
                    <li
                      key={r.record_id}
                      className="flex items-center justify-between gap-2 text-xs text-[var(--text-secondary)]"
                    >
                      <span className="text-[var(--text-tertiary)] tabular-nums">
                        {(r.created_at || '').slice(0, 10)}
                      </span>
                      <span className="flex-1 truncate">{r.type || r.kind}</span>
                      <span className={`font-mono ${income ? 'text-[var(--color-success)]' : 'text-[var(--text-primary)]'}`}>
                        {income ? '+' : '−'}${r.amount} {r.currency}
                      </span>
                      <span className="text-[var(--text-tertiary)] w-16 text-right">{r.status}</span>
                    </li>
                  );
                })}
              </ul>
              )}
            </div>
          )}
        </div>
      )}

      {/* Muted footer — charging order + scope + sandbox note */}
      <div className="px-4 py-3 border-t border-[var(--border-subtle)] bg-[var(--bg-sunken)] text-[11px] text-[var(--text-tertiary)] leading-relaxed space-y-1.5">
        <div>
          <span className="text-[var(--text-secondary)]">{t('settings.netmind.deductTitle', 'How usage is charged')}: </span>
          {t('settings.netmind.deductOrder',
            'subscription grant first → then account balance (recharge) → paid usage stops when both run out.')}
        </div>
        <div>
          {t('settings.netmind.scopeNote',
            'These NetMind.AI Power credits cover LLM API usage. Compute (GPU) and other pricing are billed separately.')}
        </div>
        <div>
          {t('settings.netmind.sandboxNotice',
            'The NarraNexus sandbox itself is free for now (no sandbox-service charge); billing will start later, with notice.')}
        </div>
      </div>
    </div>
  );
}
