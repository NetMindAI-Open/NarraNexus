/**
 * @file NetmindAccountPanel.tsx
 * @author NetMind.AI
 * @date 2026-07-02
 * @description NetMind account & subscription panel (cloud-web only).
 *
 * Single "Account & Subscription" card. Absorbs the platform free-tier view
 * (formerly the standalone QuotaPanel) so all of "what do I have / how is usage
 * paid" lives in one place, told as one story.
 *
 * Two orthogonal dimensions drive the UI:
 *   - PLAN state (resolveState): free / pro_active / pro_cancelled — top status
 *     line, badge, and management action (subscribe / cancel / resume).
 *   - RUNWAY health (deriveRunway): healthy / low — whether the panel stays calm
 *     or promotes ONE contextual action. Upsell-to-Pro appears only at
 *     (free × low); a Pro user who is low gets top-up instead; a cancelled Pro
 *     user always gets resume.
 *
 * Progressive disclosure: in a healthy state the spend controls (subscribe /
 * top-up) are hidden behind a "Manage" link so a fresh user is never asked to
 * make a billing decision on day one. Charging waterfall (free tier → grant →
 * balance; authoritative order is backend/NetMind) is stated in the runway view.
 *
 * Module F (which provider runs NarraNexus) is auto-registered by the backend on
 * login, so this panel only reflects a read-only status; switching providers
 * lives in the LLM Providers section.
 *
 * Payment return has no deterministic desktop signal, so we refresh on window
 * focus + poll with a bounded window (C3 mitigation). Copy is fully i18n-keyed
 * (settings.netmind.*) with English source defaults.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui';
import { api } from '@/lib/api';
import { platform } from '@/lib/platform';
import type {
  FeeInfo,
  FinanceRecord,
  QuotaMeResponse,
  SubscriptionMe,
  SubscriptionPlan,
} from '@/types';
import { useConfigStore } from '@/stores/configStore';
import { deriveRunway } from './netmindRunway';
import {
  money,
  freeTierPctLeft,
  freeTierTokensLeft,
  formatTokens,
  formatPeriod,
  formatDate,
} from './netmindFormat';
import { NetmindRunwayView } from './NetmindRunwayView';
import { NetmindActionZone } from './NetmindActionZone';
import { NetmindTopUpControls, type RechargeState } from './NetmindTopUpControls';

type PanelState = 'loading' | 'error' | 'free' | 'pro_active' | 'pro_cancelled';

const POLL_INTERVAL_MS = 4000;
const POLL_MAX_MS = 180000; // 3 min bound — never poll forever

// Whether the user's NetMind account is wired in as a provider (module F).
// Auto-registered by the backend on login, so this is a read-only status:
// we just report what GET /api/providers shows.
// Connection status, precise about whether NetMind is ACTUALLY driving:
//  - 'driving'       netmind provider exists AND the agent slot resolves to it
//                    → the "no setup needed" green ✓ is truthful.
//  - 'available'     netmind provider exists but the agent slot is someone
//                    else's provider → NetMind is linked-but-idle; must NOT
//                    claim "running on NetMind" (that would mislead a user who
//                    configured their own provider).
//  - 'not_connected' no netmind provider (read OK) → actionable (re-login/add).
//  - 'error'         the GET /api/providers read itself failed → transient
//                    (refresh, NOT re-login).
type NetmindStatus = 'checking' | 'driving' | 'available' | 'not_connected' | 'error';

function resolveState(me: SubscriptionMe | null): PanelState {
  if (!me) return 'error';
  const sub = me.subscription;
  if (!sub) return 'free'; // S1
  if (sub.status === 'ACTIVE' && sub.auto_renew) return 'pro_active'; // S2
  if (sub.status === 'ACTIVE' && !sub.auto_renew) return 'pro_cancelled'; // S3
  return 'free';
}

function errMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function NetmindAccountPanel() {
  const { t } = useTranslation();
  // This panel is a NetMind ("Power") account feature. Gate on whether THIS
  // session is a Power account (holds a NetMind loginToken) rather than on the
  // deployment mode, so it shows for a Power user on a local dual-mode install
  // and stays hidden for a pure-local username user.
  const isPowerUser = !!useConfigStore((s) => s.netmindToken);
  // Account identity (the "Account" half of the page title) — NetMind nickname
  // + email, so the user can see WHICH account they're logged into.
  const displayName = useConfigStore((s) => s.displayName);
  const email = useConfigStore((s) => s.email);
  const [me, setMe] = useState<SubscriptionMe | null>(null);
  const [state, setState] = useState<PanelState>('loading');
  const [busy, setBusy] = useState(false); // an action is in flight
  const [polling, setPolling] = useState(false); // awaiting payment return
  const [actionError, setActionError] = useState<string | null>(null);
  const [fee, setFee] = useState<FeeInfo | null>(null);
  const [feeLoaded, setFeeLoaded] = useState(false);
  const [records, setRecords] = useState<FinanceRecord[]>([]);
  const [quota, setQuota] = useState<QuotaMeResponse | null>(null);
  const [plans, setPlans] = useState<SubscriptionPlan[] | null>(null);
  // Top-up (module E): selected preset tier + optional custom amount override.
  const [tier, setTier] = useState<number>(10);
  const [custom, setCustom] = useState<string>('');
  const [rechargeState, setRechargeState] = useState<RechargeState>('idle');
  const [rechargeError, setRechargeError] = useState<string | null>(null);
  const [showActivity, setShowActivity] = useState(false); // recent activity collapsed by default
  const [linkBusy, setLinkBusy] = useState(false); // use-subscription link in flight
  const [linkError, setLinkError] = useState<string | null>(null);
  // Module F: read-only connection status (backend auto-registers on login).
  const [netStatus, setNetStatus] = useState<NetmindStatus>('checking');
  const mounted = useRef(true);
  // Synchronous locks: React state (busy/polling) updates are async/batched, so
  // a fast double-click can fire a handler twice before `disabled` re-renders.
  // Refs flip synchronously and are the real guard against duplicate
  // subscribe → duplicate Stripe checkout sessions.
  const busyRef = useRef(false);
  const pollingRef = useRef(false);
  const rechargeRef = useRef(false); // synchronous double-submit guard
  const linkBusyRef = useRef(false); // sync guard for the use-subscription link
  // Latest linkNetmind, readable from callbacks declared before it (TDZ).
  const linkNetmindRef = useRef<(() => Promise<void>) | null>(null);
  // Identifies the active top-up attempt. Bumping it invalidates any in-flight
  // poll loop (used to stop waiting / supersede) so a stale loop can never
  // overwrite the UI or block a fresh attempt.
  const rechargeGenRef = useRef(0);

  const load = useCallback(async () => {
    // Fetch subscription + balance + quota + plans concurrently; each result is
    // handled independently so one failure never blanks the rest (fee failure
    // hides the balance, quota failure hides the free-tier bar, etc.). Only a
    // FETCH failure is isolated here — every render below must stay null-safe
    // against a partial 200 payload.
    const [subR, feeR, recR, quotaR, plansR] = await Promise.allSettled([
      api.getSubscription(),
      api.getFeeInfo(),
      api.getRecords(),
      api.getMyQuota(),
      api.getPlans(),
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
    setQuota(quotaR.status === 'fulfilled' ? quotaR.value : null);
    setPlans(plansR.status === 'fulfilled' ? plansR.value.data?.plans ?? null : null);
  }, []);

  // C3 mitigation: no deterministic signal when the user returns from the
  // external Stripe window (esp. desktop). Refresh whenever the tab regains
  // focus so a completed payment reflects without a manual reload.
  useEffect(() => {
    if (!isPowerUser) return;
    const onFocus = () => void load();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [isPowerUser, load]);

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
            // Best-effort auto-link right after the payment lands: the user
            // just paid — this is the worst moment to ask them to sign out
            // and back in. Idempotent (409 = already linked).
            void linkNetmindRef.current?.();
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
      'Resume auto-renew for your NetMind.AI Power Pro subscription?'))) {
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

  // Read-only status: does a NetMind-source provider exist? The backend
  // auto-registers it on login, so there is nothing to click here — we just
  // report whether it's wired. Choosing the active provider is done in the
  // LLM Providers section.
  const refreshNetStatus = useCallback(async () => {
    try {
      const r = await api.getProviders();
      const provs = (r.data?.providers ?? {}) as Record<string, { source?: string }>;
      const slots = (r.data?.slots ?? {}) as {
        agent?: { config?: { provider_id?: string } };
      };
      const netmindIds = Object.entries(provs)
        .filter(([, p]) => p?.source === 'netmind')
        .map(([id]) => id);
      if (!mounted.current) return;
      if (netmindIds.length === 0) {
        setNetStatus('not_connected');
        return;
      }
      // Is NetMind the ACTIVE agent provider, or merely registered-and-idle?
      // Only the former earns the "running on NetMind" reassurance.
      const agentPid = slots.agent?.config?.provider_id;
      setNetStatus(agentPid && netmindIds.includes(agentPid) ? 'driving' : 'available');
    } catch {
      // The read itself failed — transient. Report 'error' (→ "refresh"), NOT
      // 'not_connected' (→ "re-login"): re-login can't fix a network blip.
      if (mounted.current) setNetStatus('error');
    }
  }, []);

  // Link the NetMind account as a provider via POST /providers/use-subscription.
  // First frontend caller of this endpoint (2026-07-20) — previously the ONLY
  // link path was a re-login, which stranded always-signed-in users whose
  // login predated the auto-provision flag (or whose login-time mint failed).
  // 409 = already linked, which is success for our purposes. linkNetmindRef
  // lets pollUntilActive (declared earlier) fire this after a subscription
  // payment lands without a TDZ/ordering hazard.
  const linkNetmind = useCallback(async () => {
    if (linkBusyRef.current) return;
    linkBusyRef.current = true;
    setLinkBusy(true);
    setLinkError(null);
    try {
      await api.useSubscription();
    } catch (e) {
      const msg = errMessage(e);
      if (!msg.includes('409')) {
        if (mounted.current) {
          setLinkError(msg);
          setLinkBusy(false);
        }
        linkBusyRef.current = false;
        return;
      }
    }
    await refreshNetStatus();
    linkBusyRef.current = false;
    if (mounted.current) setLinkBusy(false);
  }, [refreshNetStatus]);

  useEffect(() => {
    linkNetmindRef.current = linkNetmind;
  }, [linkNetmind]);

  useEffect(() => {
    mounted.current = true;
    if (isPowerUser) {
      void load();
      void refreshNetStatus();
    }
    return () => {
      mounted.current = false;
    };
  }, [isPowerUser, load, refreshNetStatus]);

  // Poll a recharge by Stripe session id until succeeded/failed (bounded). On
  // success, reload so the balance + activity reflect the new credit. `gen`
  // tags this loop; if rechargeGenRef moves on (user stopped waiting or started
  // another top-up) the loop bails without touching the UI.
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

  if (!isPowerUser) return null; // S0

  // Activity shows settled entries only — drop `pending` (abandoned checkouts
  // linger as pending until the Stripe session expires ~24h later).
  const settledRecords = records.filter(
    (r) => (r.status || '').toLowerCase() !== 'pending',
  );

  // ── Derived view model (null-safe against partial payloads) ───────────────
  const isPro = state === 'pro_active' || state === 'pro_cancelled';
  const runway = deriveRunway(quota, fee);
  const proPlan = plans?.find((p) => p.plan_id === 'pro') ?? null;
  const period = formatPeriod(proPlan?.prices?.[0]?.period, t('settings.netmind.perMonth', 'mo'));
  // ── Pro subscription-credit split (the "overflow tank" model) ────────────
  // NetMind's free_credit merges recharge + accumulated subscription grants;
  // subscription_credit lists the grant part separately (grants ACCUMULATE
  // across cycles — dev-verified). The panel splits the display:
  //   this cycle's tank = min(subscription_credit, grantPerCycle) → % bar
  //   overflow (older cycles' leftover) + recharge → the balance hero
  // Denominator is proPlan.monthly_grant_usd — NOT metrics.monthly_free_credit,
  // which returned 0.50 on dev against a real $19/cycle grant (semantics
  // unverified, see types/api.ts). Split only engages when every input is a
  // finite number; otherwise the pre-split display below stays (never a
  // negative or blank hero on an older API).
  const subCreditNum = Number(fee?.metrics?.subscription_credit);
  const freeCreditNum = Number(fee?.metrics?.free_credit);
  const grantPerCycle = Number(proPlan?.monthly_grant_usd);
  // isPro deliberately includes pro_cancelled: a cancelled-but-active sub
  // still holds its subscription_credit until the period ends, so the split
  // must keep rendering. Don't narrow this to pro_active for UI reasons.
  const subSplit =
    isPro &&
    fee?.metrics?.subscription_credit != null &&
    Number.isFinite(subCreditNum) &&
    Number.isFinite(freeCreditNum) &&
    Number.isFinite(grantPerCycle) &&
    grantPerCycle > 0;
  const subCycleRemaining = subSplit ? Math.min(subCreditNum, grantPerCycle) : 0;
  const subOverflow = subSplit ? Math.max(0, subCreditNum - grantPerCycle) : 0;
  const subPct = subSplit
    ? Math.max(0, Math.min(100, Math.floor((subCycleRemaining / grantPerCycle) * 100)))
    : null;
  // Own spendable money: recharge + overflow from earlier cycles.
  const heroValue = subSplit
    ? Math.max(0, freeCreditNum - subCreditNum) + subOverflow
    : fee?.metrics?.free_credit;

  // The free tier is a ONE-TIME registration grant — no periodic refresh
  // (staff can top it up manually, nothing else). Once used up, a permanent
  // 0% warning bar is dead weight, so the bar collapses to a single
  // explanatory line (freeTierExhausted → RunwayView note) and the flow
  // copy switches via freePct=null. For a Pro user with the split active,
  // the plan-credit bar REPLACES the free-tier display entirely (Owner call
  // 2026-07-18) — display only, the backend still drains the free tier
  // first, which just reads as "nothing is being spent yet".
  const freePctRaw = freeTierPctLeft(quota);
  const freeTierExhausted = !subSplit && freePctRaw === 0;
  const freePct = subSplit || freePctRaw === 0 ? null : freePctRaw;
  // Row value in tokens ("3.9M tokens left"), same more-depleted dimension
  // as the bar width — a percentage told users nothing about how much a
  // "percent" actually buys. Remaining only (Owner call: remaining/total
  // reads too dense); the bar carries the proportion context.
  const freeTokens = freePct !== null ? freeTierTokensLeft(quota) : null;
  const freeTokensText = freeTokens
    ? t('settings.netmind.freeTierTokensLeft', '{{remaining}} tokens left', {
        remaining: formatTokens(freeTokens.remaining),
      })
    : null;
  const grantUsd = fee?.metrics?.monthly_free_credit;
  // Legacy grant line — only for Pro accounts on an API without
  // subscription_credit (the bar replaces it once the split engages).
  const grantText =
    isPro && !subSplit && grantUsd != null && grantUsd !== ''
      ? t('settings.netmind.grantPerPeriod', '{{amount}} / {{period}}', {
          amount: `$${money(grantUsd)}`,
          period,
        })
      : null;
  // Balance is the hero (with the split: recharge + overflow; without:
  // NetMind's merged free_credit). Runway below shows the pools breakdown.
  const showBalanceHero = feeLoaded && !!fee;
  const showRunway =
    freePct !== null || !!grantText || freeTierExhausted || subPct !== null;

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

  // Top-up controls (module E) — reused inside the manage disclosure and shown
  // directly when a Pro user is low. Presentational piece lives in
  // NetmindTopUpControls; the guarded handlers stay here.
  const topUp = (
    <NetmindTopUpControls
      tier={tier}
      custom={custom}
      rechargeState={rechargeState}
      rechargeError={rechargeError}
      onSelectTier={(v) => { setTier(v); setCustom(''); }}
      onChangeCustom={setCustom}
      onRecharge={handleRecharge}
      onStopWaiting={handleStopWaitingRecharge}
    />
  );

  // One-line plan explanation shown next to the badge in the plan row — so the
  // subscription state is labelled AND explained, not a bare corner chip.
  const planExpl = (() => {
    if (state === 'pro_active') {
      return me?.subscription
        ? t('settings.netmind.planExplProActive', 'Member · valid until {{date}}', {
            date: formatDate(me.subscription.current_period_end),
          })
        : t('settings.netmind.planExplProActive', 'Member · valid until {{date}}', { date: '—' });
    }
    if (state === 'pro_cancelled' && me?.subscription) {
      return t('settings.netmind.expiresDowngrade', 'Valid until {{date}}, then downgrades to Free', {
        date: formatDate(me.subscription.current_period_end),
      });
    }
    return t('settings.netmind.planExplFree', 'Free — usage billed from your balance.');
  })();

  // Account + plan as a labelled definition list — fills the missing "Account"
  // (identity) and makes the plan explicit + explained.
  const accountAndPlan = (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3.5 gap-y-2 items-baseline">
      {email && (
        <>
          <dt className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
            {t('settings.netmind.accountLabel', 'Account')}
          </dt>
          <dd className="m-0 text-sm text-[var(--text-primary)] flex items-center gap-2 flex-wrap">
            {/* Only show the nickname when it adds info — NetMind often returns
                the email AS the displayName, which would print it twice. */}
            {displayName && displayName !== email && <span>{displayName}</span>}
            {displayName && displayName !== email && (
              <span className="text-[var(--text-tertiary)]">·</span>
            )}
            <span className="font-mono text-xs text-[var(--text-secondary)]">{email}</span>
          </dd>
        </>
      )}
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
        {t('settings.netmind.planLabel', 'Plan')}
      </dt>
      <dd className="m-0 flex items-center gap-2 flex-wrap">
        {planBadge}
        <span className="text-xs text-[var(--text-secondary)]">{planExpl}</span>
      </dd>
    </dl>
  );

  // Balance hero — the panel's key number. free_credit is grant + recharge
  // combined; label reflects that when a grant exists.
  const balanceHero = showBalanceHero ? (
    <div>
      <div className="text-3xl font-semibold font-mono tabular-nums text-[var(--text-primary)] leading-none tracking-tight">
        ${money(heroValue)}
      </div>
      <div className="mt-1.5 text-xs text-[var(--text-tertiary)]">
        {subSplit
          ? t('settings.netmind.ownBalance', 'Your balance (top-ups + carried-over plan credit)')
          : grantText
            ? t('settings.netmind.balanceUsable', 'Current usable balance')
            : t('settings.netmind.currentBalance', 'Current balance')}
      </div>
    </div>
  ) : null;

  // Connection status (module F) — the ONE reassurance ✓. Four states, only
  // not_connected is actionable (agents can't run on NetMind until fixed); a
  // transient fetch failure ('error') must NOT tell the user to re-login.
  const connectionStatus = () => {
    if (netStatus === 'driving') {
      return (
        <div className="flex items-center gap-1.5 text-sm font-medium text-[var(--color-success)]">
          <span aria-hidden>✓</span>
          <span>
            {t('settings.netmind.netDriving',
              'Running on your NetMind.AI Power account — no setup needed.')}
          </span>
        </div>
      );
    }
    if (netStatus === 'available') {
      // Registered but idle — the user is on their own provider. Neutral (no green
      // ✓), and point to where they'd switch TO NetMind if they want.
      return (
        <p className="text-xs text-[var(--text-tertiary)]">
          {t('settings.netmind.netAvailable',
            'Your NetMind.AI Power account is linked but idle — you’re running on your own provider. Switch in Model Defaults.')}
        </p>
      );
    }
    if (netStatus === 'not_connected') {
      // Actionable: the link button calls POST /providers/use-subscription —
      // no more "sign out and back in" busywork (that path still works and
      // stays as the login-time auto-heal, this is just the in-session exit).
      return (
        <div className="rounded-md bg-[var(--color-warning)]/10 p-3 text-sm text-[var(--color-warning)] space-y-2">
          <div className="flex items-center justify-between gap-3">
            <p className="m-0">
              {t('settings.netmind.notConnected',
                'Your NetMind.AI Power account isn’t linked as a provider yet.')}
            </p>
            <Button
              size="sm"
              variant="accent"
              onClick={linkNetmind}
              disabled={linkBusy}
              className="shrink-0"
            >
              {linkBusy
                ? t('settings.netmind.working', 'Working…')
                : t('settings.netmind.linkNow', 'Link it now')}
            </Button>
          </div>
          {linkError && (
            <p className="m-0 text-xs">
              {t('settings.netmind.linkFailed', 'Linking failed:')} {linkError}
            </p>
          )}
        </div>
      );
    }
    if (netStatus === 'error') {
      return (
        <p className="text-xs text-[var(--text-tertiary)]">
          {t('settings.netmind.netStatusError',
            'Couldn’t read your connection status. Refresh to retry.')}
        </p>
      );
    }
    // checking
    return (
      <p className="text-xs text-[var(--text-tertiary)]">
        {t('settings.netmind.checkingStatus', 'Checking your NetMind.AI Power connection…')}
      </p>
    );
  };

  return (
    <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] overflow-hidden">
      {/* Header — product brand only (plan badge moved into the plan row) */}
      <div className="px-4 py-3 border-b border-[var(--border-subtle)]">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">NetMind.AI Power</h3>
        <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
          {t('settings.netmind.subtitle', 'Power plan & credits · used for your LLM API usage')}
        </p>
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
          {/* 1 · identity/setup group: account + plan + connection status. These
              answer "who am I / how am I set up"; keeping the connection line
              here (not wedged above the runway) stops the small 'available'
              variant from crowding the free-tier row, and reads as a status
              summary. */}
          {accountAndPlan}
          {connectionStatus()}

          <div className="border-t border-[var(--border-subtle)]" />

          {/* 2 · money block: balance hero + runway breakdown, uninterrupted. */}
          {balanceHero}

          {/* 3 · runway — pools breakdown (free tier / grant) + charging
              order. Balance itself is the hero above, not here. The free
              tier is always drawn first (platform behavior, no toggle). */}
          {showRunway && (
            <NetmindRunwayView
              freePct={freePct}
              freeTokensText={freeTokensText}
              grantText={grantText}
              freeTierExhausted={freeTierExhausted}
              subPct={subPct}
              flowIsPro={isPro}
            />
          )}
          {/* eligible=false forces runway low, and the low action zone already
              says "you're out of credits — do X" in plain words; stacking this
              system-toned warning on top reads like an error and duplicates the
              prompt. Only render it when no low prompt is shown (pro_cancelled,
              whose action zone talks about auto-renew instead). */}
          {feeLoaded && fee?.eligible === false
            && !(runway === 'low' && state !== 'pro_cancelled') && (
            <div className="text-xs text-[var(--color-warning)]">
              {t('settings.netmind.notEligible',
                'Cannot incur paid usage right now (no balance / not eligible).')}
            </div>
          )}
          {feeLoaded && fee?.checks?.has_arrears && (
            <div className="text-xs text-[var(--color-error)]">
              {t('settings.netmind.hasArrears', 'You have outstanding arrears.')}
            </div>
          )}

          {/* 3 · action zone (plan × runway) */}
          <NetmindActionZone
            state={state}
            runway={runway}
            freeTierExhausted={freeTierExhausted}
            busy={busy}
            polling={polling}
            proPlan={proPlan}
            topUp={topUp}
            onSubscribe={handleSubscribe}
            onCancel={handleCancel}
            onReactivate={handleReactivate}
          />
          {polling && (
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.awaitingPayment',
                'Waiting for payment to complete… this panel refreshes automatically. If you already paid, come back to this tab.')}
            </p>
          )}
          {actionError && <p className="text-xs text-[var(--color-error)]">{actionError}</p>}

          {/* 4 · recent activity — collapsed by default, settled ledger only.
              `pending` rows are hidden: an abandoned checkout leaves a pending
              record that only flips to failed ~24h later, so showing them piles
              up noise; in-progress payment is already surfaced by the live
              "waiting" state above. */}
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

      {/* Muted footer — scope + sandbox note (charging order now lives in the
          runway view, next to the balances it describes) */}
      <div className="px-4 py-3 border-t border-[var(--border-subtle)] bg-[var(--bg-sunken)] text-[11px] text-[var(--text-tertiary)] leading-relaxed space-y-1.5">
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
