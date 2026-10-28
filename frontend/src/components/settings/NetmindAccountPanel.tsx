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
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Button, useConfirm } from '@/components/ui';
import { api } from '@/lib/api';
import { captureProductEvent } from '@/lib/productAnalytics';
import { platform } from '@/lib/platform';
import type {
  FeeInfo,
  FinanceRecord,
  FxQuote,
  SubscribePaymentMethod,
  QuotaMeResponse,
  RechargePaymentMethod,
  SubscriptionMe,
  SubscriptionPlan,
} from '@/types';
import { useConfigStore } from '@/stores/configStore';
import { deriveRunway } from './netmindRunway';
import {
  money,
  moneyOrNull,
  creditMoney,
  freeTierPctLeft,
  freeTierCreditLeft,
  formatPeriod,
  formatDate,
} from './netmindFormat';
import { NetmindRunwayView } from './NetmindRunwayView';
import { NetmindActionZone } from './NetmindActionZone';
import { NetmindTopUpControls, type RechargeState } from './NetmindTopUpControls';
import { NetmindProPurchase } from './NetmindProPurchase';
import { NetmindReturnNotice } from './NetmindReturnNotice';
import { NarraUsageSection } from './NarraUsageSection';
import { useNetmindPaymentReturn } from './useNetmindPaymentReturn';

type PanelState =
  | 'loading'
  | 'error'
  | 'free'
  | 'pro_active'
  | 'pro_cancelled'
  | 'pro_onetime';

const POLL_INTERVAL_MS = 4000;
const POLL_MAX_MS = 180000; // 3 min bound — never poll forever
// A custom amount is typed a digit at a time; without this every keystroke is
// its own exchange-rate request.
const FX_DEBOUNCE_MS = 400;

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
  if (sub.status !== 'ACTIVE') return 'free';
  // S4 must be tested BEFORE the auto_renew split. A one-time (Alipay/WeChat)
  // purchase never renews, so it reports auto_renew=false for its entire life
  // and would otherwise read as "cancelled card" — which would offer it
  // "Resume auto-renew", an action that does not exist for it.
  //
  // An ABSENT payment_method means card: every subscription older than the
  // nexus account is one, and that is exactly what the two lines below already
  // assumed before this state existed.
  if (sub.payment_method && sub.payment_method !== 'stripe') return 'pro_onetime';
  if (sub.auto_renew) return 'pro_active'; // S2
  return 'pro_cancelled'; // S3
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
  // In-app confirmation. NEVER window.confirm: wry (the Tauri webview) does not
  // render the native dialogs, so the call resolves falsy and the handler bails
  // out silently — on the DMG the button would simply do nothing (rule #7: the
  // two run modes must behave identically). See ui/ConfirmDialog.
  const { confirm, dialog: confirmDialog } = useConfirm();
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
  // Card is the default rail because it is the one that needs no explanation
  // and no currency conversion. All three stay offered regardless of locale.
  const [payMethod, setPayMethod] = useState<RechargePaymentMethod>('default');
  // The quote is kept WITH the amount it describes. Two different lifetimes
  // live in one reply: the conversion ("$10 ≈ ¥73") expires the moment the
  // amount changes, while the minimum is a property of the WeChat rail and
  // does not. Clearing the whole thing on every keystroke dropped the floor
  // too, and a fast typist could then submit an under-minimum amount straight
  // into an upstream 400 — which is the exact thing the floor exists to avoid.
  const [fx, setFx] = useState<{ quote: FxQuote; forAmount: number } | null>(null);
  // One-time (Alipay/WeChat) renewal. Separate from the top-up amount state:
  // they are different purchases with different rails and must not share a
  // debounce, a poll generation or an error line.
  const [payFlow, setPayFlow] = useState<'topup' | 'renew'>('topup');
  // `/pay` — where the marketing pricing CTA lands — redirects here instead of
  // minting a card checkout of its own, so the rail choice has exactly one
  // implementation. Arriving with this intent has to OPEN that choice: landing
  // on a settings page with the purchase one click away would move the dead
  // end rather than remove it.
  const [routeParams] = useSearchParams();
  const buyIntent = routeParams.get('intent') === 'buy';
  const [buyMonths, setBuyMonths] = useState(1);
  // All three rails are legal here: a FREE user starts Pro from this same
  // control and card is one of their options. What removes card while a
  // one-time subscription is live is `allowCard` on the control, NOT the type —
  // that restriction is a fact about upstream's state, not about the value.
  const [buyMethod, setBuyMethod] = useState<SubscribePaymentMethod>('stripe');
  const [renewFx, setRenewFx] = useState<{ quote: FxQuote; forAmount: number } | null>(null);

  const [fxLoading, setFxLoading] = useState(false);
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

  // (handleSubscribe lived here. It had become a line-for-line copy of
  // handleBuyPro's card branch, and its only remaining consumer was
  // NetmindUpsellCard in `subscribed` mode — which renders no CTA, so the
  // callback could never fire. Two implementations of the same purchase means
  // the next change to it lands in one of them, so it is deleted rather than
  // kept "just in case".)

  const handleCancel = useCallback(async () => {
    if (busyRef.current) return;
    // Labels spell out the two outcomes instead of Confirm/Cancel: next to a
    // subscription-CANCELLING action, a button labelled "Cancel" is genuinely
    // ambiguous about which cancel it means.
    const ok = await confirm({
      title: t('settings.netmind.cancelConfirmTitle', 'Turn off auto-renew?'),
      message: t('settings.netmind.cancelConfirm',
        'Cancel = turn off auto-renew. You stay on Nexus Pro until the period ends — no immediate downgrade, no prorated refund. Continue?'),
      confirmText: t('settings.netmind.cancelConfirmAction', 'Turn off auto-renew'),
      cancelText: t('settings.netmind.cancelConfirmKeep', 'Keep subscription'),
      danger: true,
    });
    if (!ok) return;
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
  }, [t, load, confirm]);

  const handleReactivate = useCallback(async () => {
    if (busyRef.current) return;
    // reactivate re-enables auto-renew (may trigger a charge) — confirm, since
    // its exact billing semantics are still pending NetMind confirmation.
    const ok = await confirm({
      title: t('settings.netmind.reactivateConfirmTitle', 'Resume auto-renew?'),
      message: t('settings.netmind.reactivateConfirm',
        'Resume auto-renew for your Nexus Pro subscription?'),
      confirmText: t('settings.netmind.reactivateConfirmAction', 'Resume'),
      cancelText: t('settings.netmind.reactivateConfirmDismiss', 'Not now'),
    });
    if (!ok) return;
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
  }, [t, load, confirm]);

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

  // Post-payment return: consume Stripe's query params, then let the hook drive
  // the delayed money re-read and (for a subscription) the bounded plan-flip
  // poll. Both callbacks must stay referentially stable — see the hook's
  // docstring for why. Declared after them so neither is read before init.
  const returnNotice = useNetmindPaymentReturn(isPowerUser, load, pollUntilActive);

  // The amount the top-up controls currently describe. ONE derivation, read by
  // both the quote effect and the submit handler, so the number we quote and
  // the number we charge can never come from two different rules.
  // Number() (not parseFloat) so "5abc" → NaN is rejected, not silently 5.
  const selectedAmount = custom.trim() ? Number(custom.trim()) : tier;

  // WeChat settles in CNY, so the payer needs to see what their bank will
  // actually take before they commit. Two rules make this safe:
  //   * the quote is stored WITH the amount it was fetched for, and only the
  //     render path requires those to match (see the `fx` prop below). A stale
  //     "$10 ≈ ¥73" therefore never sits under a $25 input, while the rail's
  //     minimum — which does not depend on the amount — survives the change.
  //   * a failed quote is swallowed. It is a display helper — refusing to let
  //     someone pay because it 502'd would turn a cosmetic outage into a
  //     revenue one, and upstream converts and enforces its own minimum anyway.
  useEffect(() => {
    if (payMethod !== 'wechat') {
      setFx(null); // leaving the rail drops the floor with it, correctly
      setFxLoading(false);
      return;
    }
    if (!Number.isFinite(selectedAmount) || selectedAmount <= 0) {
      setFxLoading(false);
      return;
    }
    setFxLoading(true);
    let cancelled = false;
    // Debounced: a custom amount is typed a digit at a time and each keystroke
    // would otherwise be its own request.
    const timer = setTimeout(async () => {
      try {
        const r = await api.fxRate(selectedAmount);
        if (!cancelled && mounted.current && r.data) {
          setFx({ quote: r.data, forAmount: selectedAmount });
        }
      } catch {
        /* quote unavailable — see above; the payment path stays open */
      } finally {
        if (!cancelled && mounted.current) setFxLoading(false);
      }
    }, FX_DEBOUNCE_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [payMethod, selectedAmount]);

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
    const amount = selectedAmount;
    if (!Number.isFinite(amount) || amount <= 0) {
      setRechargeState('failed');
      setRechargeError(
        t('settings.netmind.rechargeInvalidAmount', 'Enter an amount greater than 0.'),
      );
      return;
    }
    // WeChat has a floor upstream rejects with a 400. When we have a quote,
    // stop it here instead: a payer who clicked through to a QR code only to be
    // bounced has already lost trust in the flow.
    const minUsd = Number(fx?.quote.min_amount_usd);
    if (payMethod === 'wechat' && Number.isFinite(minUsd) && amount < minUsd) {
      setRechargeState('failed');
      setRechargeError(
        t('settings.netmind.rechargeBelowMinimum',
          'WeChat has a minimum of about ${{min}} per payment.',
          { min: minUsd.toFixed(2) }),
      );
      return;
    }
    rechargeRef.current = true;
    const gen = ++rechargeGenRef.current; // this attempt owns the poll
    setPayFlow('topup');
    setRechargeState('processing');
    setRechargeError(null);
    try {
      const r = await api.recharge(amount, payMethod);
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
  }, [selectedAmount, payMethod, fx, t, pollRechargeStatus]);

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

  const proPlan = plans?.find((p) => p.plan_id === 'pro') ?? null;

  // The control must never render a rail it does not offer. `buyMethod` starts
  // at 'stripe' (the right default for a free user), but card is withdrawn once
  // a one-time subscription is live — and without this the extend dialog drew
  // the CARD form with no card option in sight: no month grid, and a button
  // reading "Subscribe". Normalised in ONE place so the form, the total, the
  // exchange-rate quote and the request cannot disagree about which rail this is.
  const cardAllowed = state !== 'pro_onetime';
  const buyMethodEffective: SubscribePaymentMethod =
    !cardAllowed && buyMethod === 'stripe' ? 'alipay' : buyMethod;

  // The one-time total is built from what a month COSTS, never from the grant:
  // they are equal today, and a change to either would otherwise silently
  // mis-price a 12-month checkout. Falls back to the grant only because the
  // catalog did not always carry a price field.
  const monthlyPriceUsd = (() => {
    const p = proPlan?.usd_monthly_price ?? proPlan?.monthly_grant_usd;
    return Number.isFinite(Number(p)) ? Number(p) : null;
  })();
  const renewTotalUsd =
    monthlyPriceUsd != null
      ? monthlyPriceUsd * (buyMethodEffective === 'stripe' ? 1 : buyMonths)
      : null;

  // A WeChat renewal is charged in CNY, so quote the total the way the top-up
  // flow quotes its amount — debounce included.
  //
  // This used to say a debounce was unnecessary "because the month grid changes
  // on discrete clicks, not keystrokes". That stopped being true in the same
  // change that wrote it: the grid is a radiogroup now, so holding an arrow key
  // walks 1→12 and fires six quotes at an endpoint deliberately left uncached.
  // A justification its own file can falsify is worse than none — the next
  // person builds on it. The keyboard path must not be the one that hammers
  // upstream, least of all right after making it the accessible one.
  useEffect(() => {
    if (buyMethodEffective !== 'wechat' || renewTotalUsd == null) {
      setRenewFx(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const r = await api.fxRate(renewTotalUsd);
          if (!cancelled && mounted.current && r.data) {
            setRenewFx({ quote: r.data, forAmount: renewTotalUsd });
          }
        } catch {
          // Display helper only. Refusing to let someone pay because the quote
          // 502'd would turn a cosmetic outage into a revenue one; upstream
          // does its own conversion regardless.
        }
      })();
    }, FX_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [buyMethodEffective, renewTotalUsd]);

  // ONE entry for both "start Pro" and "extend a one-time Pro": they are the
  // same question — how do you want to pay for Pro — asked in two states, and
  // splitting them is exactly what left Alipay/WeChat users unable to subscribe
  // at all while the branch was named after letting them.
  //
  // The rails then need different guards AND different polls, because upstream
  // models them as different things:
  //   card      a real subscription -> busyRef, poll /me until ACTIVE
  //   one-time  a RECHARGE          -> rechargeRef, poll that session
  // Polling /me for an EXTENSION would report success on the first tick (the
  // subscription is already ACTIVE), so the session poll is not an optimisation
  // here, it is the only reading that can be true.
  const handleBuyPro = useCallback(async () => {
    const oneTime = buyMethodEffective !== 'stripe';
    if (oneTime ? rechargeRef.current : busyRef.current) return;
    captureProductEvent('subscribe_clicked');

    if (!oneTime) {
      busyRef.current = true;
      setBusy(true);
      setActionError(null);
      try {
        const r = await api.subscribe();
        const url = r.data?.checkout_url;
        if (!url) throw new Error('No checkout URL returned');
        captureProductEvent('checkout_opened');
        await platform.openExternal(url);
        void pollUntilActive();
      } catch (e) {
        if (mounted.current) setActionError(errMessage(e));
      } finally {
        busyRef.current = false;
        if (mounted.current) setBusy(false);
      }
      return;
    }

    rechargeRef.current = true;
    const gen = ++rechargeGenRef.current;
    setPayFlow('renew');
    setRechargeState('processing');
    setRechargeError(null);
    try {
      const r = await api.subscribe(buyMethodEffective, buyMonths);
      const url = r.data?.checkout_url;
      const sid = r.data?.session_id;
      if (!url || !sid) throw new Error('No checkout URL returned');
      captureProductEvent('checkout_opened');
      await platform.openExternal(url);
      void pollRechargeStatus(sid, gen);
    } catch (e) {
      if (mounted.current && rechargeGenRef.current === gen) {
        setRechargeState('failed');
        setRechargeError(errMessage(e));
      }
      rechargeRef.current = false;
    }
  }, [buyMethodEffective, buyMonths, pollRechargeStatus, pollUntilActive]);

  if (!isPowerUser) return null; // S0

  // Activity shows settled entries only — drop `pending` (abandoned checkouts
  // linger as pending until the Stripe session expires ~24h later).
  const settledRecords = records.filter(
    (r) => (r.status || '').toLowerCase() !== 'pending',
  );

  // ── Derived view model (null-safe against partial payloads) ───────────────
  const isPro =
    state === 'pro_active' || state === 'pro_cancelled' || state === 'pro_onetime';
  const runway = deriveRunway(quota, fee);
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
  // Row value in dollars ("$7.423919 left") — the wallet's own unit, so it
  // means the same thing as the balance hero below it. Remaining only (Owner
  // call: remaining/total reads too dense); the bar carries the proportion.
  // creditMoney, not money: see its docstring — at two decimals a session of
  // free-tier use rounds to nothing and the grant looks stuck at 10.00.
  const freeCredit = freePct !== null ? freeTierCreditLeft(quota) : null;
  const freeTokensText = freeCredit
    ? t('settings.netmind.freeTierCreditLeft', '${{remaining}} left', {
        remaining: creditMoney(freeCredit.remaining),
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
          {t('settings.netmind.planPro', 'Nexus Pro')}
        </span>
      );
    }
    if (state === 'pro_cancelled') {
      return (
        <span className="shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full bg-[var(--color-warning)]/12 text-[var(--color-warning)]">
          {t('settings.netmind.badgeCancelled', 'Nexus Pro · ending')}
        </span>
      );
    }
    // Same badge as an auto-renewing Pro, deliberately NOT the warning one
    // pro_cancelled gets. "Ending" is a state a card subscriber chose and can
    // undo; for a one-time purchase it is simply what the product IS, for its
    // whole life. A permanent warning chip on a normal, paid-up state is how
    // you train someone to stop reading warnings. The end date and the
    // does-not-renew fact live in planExpl, right next to it.
    if (state === 'pro_onetime') {
      return (
        <span className="shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full bg-[var(--accent-primary)]/12 text-[var(--accent-primary)]">
          {t('settings.netmind.planPro', 'Nexus Pro')}
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

  // Every spend control disables while ANY of them is mid-flight: the guards
  // are refs and cannot reach the render, so a control that is merely NOT the
  // narrator would stay clickable and do nothing at all when clicked.
  const anyMoneyBusy = busy || polling || rechargeState === 'processing';

  const proPurchase = (
    <NetmindProPurchase
      allowCard={cardAllowed}
      months={buyMonths}
      onChangeMonths={setBuyMonths}
      payMethod={buyMethodEffective}
      onChangePayMethod={setBuyMethod}
      monthlyPriceUsd={monthlyPriceUsd}
      chargeAmountCny={
        renewFx && renewFx.forAmount === renewTotalUsd
          // moneyOrNull, not Number().toFixed(): charge_amount is optional on
          // the quote, and an unguarded conversion puts "¥NaN" next to the
          // total on the line read last before paying.
          ? moneyOrNull(renewFx.quote.charge_amount)
          : null
      }
      currentPeriodEnd={me?.subscription?.current_period_end}
      state={payFlow === 'renew' ? rechargeState : 'idle'}
      busy={anyMoneyBusy}
      error={payFlow === 'renew' && rechargeState === 'failed' ? rechargeError : null}
      onPay={handleBuyPro}
    />
  );

  // Top-up controls (module E) — reused inside the manage disclosure and shown
  // directly when a Pro user is low. Presentational piece lives in
  // NetmindTopUpControls; the guarded handlers stay here.
  const topUp = (
    <NetmindTopUpControls
      tier={tier}
      custom={custom}
      rechargeState={payFlow === 'topup' ? rechargeState : 'idle'}
      busy={anyMoneyBusy}
      rechargeError={payFlow === 'topup' ? rechargeError : null}
      paymentMethod={payMethod}
      fx={fx && fx.forAmount === selectedAmount ? fx.quote : null}
      fxLoading={fxLoading}
      onChangePaymentMethod={setPayMethod}
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
    if (state === 'pro_onetime' && me?.subscription) {
      return t('settings.netmind.planExplOnetime',
        'Valid until {{date}} — one-time purchase, does not renew', {
          date: formatDate(me.subscription.current_period_end),
        });
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
      {/* The hero is the number people watch move, so the "which wallet is
          this" caveat belongs ON it, not in a footnote. Any NetMind product on
          this account draws it down — that is the whole confusion this line
          exists to pre-empt. */}
      <div className="mt-1 text-[11px] text-[var(--text-tertiary)]">
        {t('settings.netmind.balanceScope',
          'NetMind account balance — anything you run on this NetMind account draws it down, including outside NarraNexus.')}
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
        <div className="rounded-[var(--radius-md)] bg-[var(--color-warning)]/10 p-3 text-sm text-[var(--color-warning)] space-y-2">
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
    <div className="rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] overflow-hidden">
      {/* Header — product brand only (plan badge moved into the plan row) */}
      <div className="px-4 py-3 border-b border-[var(--border-subtle)]">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">NetMind.AI Power</h3>
        {/* Names the SCOPE, not just the product. Everything below comes from
            NetMind's finance domain, which is per-ACCOUNT: the old subtitle
            ("used for your LLM API usage") let a balance drop caused by another
            NetMind product read as NarraNexus usage. */}
        <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
          {t('settings.netmind.subtitle',
            'Your NetMind.AI account · plan & credits shared by every NetMind product, not only NarraNexus')}
        </p>
      </div>

      {/* Answers the question the payer arrived with, so it sits above the
          loading/error branches — see NetmindReturnNotice. */}
      {returnNotice && <NetmindReturnNotice notice={returnNotice} />}

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

          {/* 4 · action zone (plan × runway) */}
          <NetmindActionZone
            state={state}
            runway={runway}
            freeTierExhausted={freeTierExhausted}
            busy={busy}
            polling={polling}
            proPlan={proPlan}
            topUp={topUp}
            proPurchase={proPurchase}
            openBuyOnMount={buyIntent}
            onCancel={handleCancel}
            onReactivate={handleReactivate}
          />
          {/* Suppressed on a successful return: the payer is standing here
              having already paid, and "waiting for payment to complete" would
              contradict the notice above. The poll is still running. */}
          {polling && returnNotice?.status !== 'success' && (
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.awaitingPayment',
                'Waiting for payment to complete… this panel refreshes automatically. If you already paid, come back to this tab.')}
            </p>
          )}
          {actionError && <p className="text-xs text-[var(--color-error)]">{actionError}</p>}

          {/* 5 · what NarraNexus itself consumed. Sits directly above the
              NetMind account ledger because the two answer adjacent questions
              and are constantly mistaken for each other: this one is scoped to
              this platform and measured in tokens, the one below is the whole
              account measured in money. Self-hiding when empty/unavailable. */}
          <NarraUsageSection />

          {/* 6 · recent activity — collapsed by default, settled ledger only.
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
                {t('settings.netmind.activityTitle', 'NetMind account activity (all products)')}
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
            'These NetMind.AI Power credits cover LLM API usage across every NetMind product and API key on this account — the balance and activity above are account-wide, not NarraNexus-only. Compute (GPU) and other pricing are billed separately.')}
        </div>
        <div>
          {t('settings.netmind.sandboxNotice',
            'The NarraNexus sandbox itself is free for now (no sandbox-service charge); billing will start later, with notice.')}
        </div>
      </div>

      {/* Must be mounted for `confirm()` to ever resolve — without it the
          promise hangs and the action button does nothing. Dialog portals to
          body, so its position here is not a layout concern. */}
      {confirmDialog}
    </div>
  );
}
