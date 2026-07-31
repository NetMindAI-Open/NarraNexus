/**
 * @file useNetmindPaymentReturn.ts
 * @date 2026-07-30
 * @description Consumes the post-payment return query params Stripe lands the
 * payer on, and drives the two follow-ups a fresh return tab owes them.
 *
 * Stripe sends the payer to `?tab=account&status=success|cancelled&flow=…` —
 * a URL the backend put on the Checkout Session (backend/routes/billing.py
 * ::_return_urls). Before that existed they finished on NetMind's own result
 * page, on a domain they had never seen (the 2026-07-30 P0 report).
 *
 * Extracted from NetmindAccountPanel, which had grown past the 800-line ceiling;
 * it already hosts three sibling components split out the same way.
 */

import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

/** Which payment the user is returning from. */
export type ReturnFlow = 'subscription' | 'topup';

/**
 * How the payment ended, as reported by the redirect. `flow` may be absent on a
 * Checkout Session created before this shipped, so copy must degrade to
 * something that is still true without it.
 */
export type ReturnNotice = {
  status: 'success' | 'cancelled';
  flow: ReturnFlow | null;
};

/** Grace period before re-reading money state: Stripe redirects as soon as IT
 * is done, a beat before NetMind has credited the account. */
const RETURN_SETTLE_MS = 3000;

/**
 * Read the redirect's verdict, or null when these params are not a payment
 * return. `status` is the sole trigger: an unrecognised value is ignored rather
 * than rendered as an empty notice. Pure, so the rule lives in one obvious place.
 */
function parseReturnNotice(params: URLSearchParams): ReturnNotice | null {
  const status = params.get('status');
  if (status !== 'success' && status !== 'cancelled') return null;
  const rawFlow = params.get('flow');
  const flow: ReturnFlow | null =
    rawFlow === 'subscription' || rawFlow === 'topup' ? rawFlow : null;
  return { status, flow };
}

/**
 * @param enabled  false keeps the hook inert (non-Power session — nobody pays).
 *                 Read on the first render only, like the `?tab=` handling in
 *                 SettingsPage; the store it comes from hydrates synchronously.
 * @param refresh  re-read money state from the server. MUST be referentially
 *                 stable (a fresh identity every render would re-arm the settle
 *                 timer forever and it would never fire).
 * @param watchPlanFlip  start the bounded poll that observes the plan turning
 *                 Pro. Same stability requirement.
 * @returns the notice to render, or null when this mount is not a return.
 */
export function useNetmindPaymentReturn(
  enabled: boolean,
  refresh: () => void,
  watchPlanFlip: () => void,
): ReturnNotice | null {
  const [searchParams, setSearchParams] = useSearchParams();
  // Parsed during the FIRST render rather than in an effect. Two reasons: the
  // notice is the thing the payer arrived to see, so it belongs in the first
  // paint instead of one cascading render later; and useState's initializer is
  // "exactly once" by construction, so no bookkeeping flag has to enforce it.
  const [notice] = useState<ReturnNotice | null>(() =>
    enabled ? parseReturnNotice(searchParams) : null,
  );
  const sideEffectsDone = useRef(false);

  // The side effects the notice owes, exactly once.
  //
  // Stripping the params is what stops a refresh from re-announcing a payment
  // that already settled. `tab` is kept — dropping it would bounce the user off
  // this pane on reload.
  //
  // A success does NOT re-read here: the panel's mount effect already fetched
  // everything in this same tick, so a second read would duplicate those
  // requests without being any fresher. A redirect is not a receipt — the read
  // that matters is the delayed one below.
  //
  // It also does not link the NetMind provider directly, even though that is
  // what the payer needs next. NetMind may not have marked the subscription
  // active yet, and a failed link would show an error to someone who just paid
  // successfully. `watchPlanFlip` owns it: the poll links on its ACTIVE branch.
  // A top-up never changes the plan, so polling /me for it would be noise.
  useEffect(() => {
    if (!notice || sideEffectsDone.current) return;
    sideEffectsDone.current = true;

    const next = new URLSearchParams(searchParams);
    next.delete('status');
    next.delete('flow');
    setSearchParams(next, { replace: true });

    if (notice.status === 'success' && notice.flow !== 'topup') watchPlanFlip();
  }, [notice, searchParams, setSearchParams, watchPlanFlip]);

  // Own effect keyed on `notice`. Putting this timer in the effect above would
  // arm it and then immediately clear it, because stripping the query re-runs
  // that effect and cleanup fires first. Cleared on unmount — an orphan timer
  // firing into a dead tree is the fire-and-forget shape the incident notes
  // warn about.
  useEffect(() => {
    if (notice?.status !== 'success') return;
    const timer = setTimeout(refresh, RETURN_SETTLE_MS);
    return () => clearTimeout(timer);
  }, [notice, refresh]);

  return notice;
}
