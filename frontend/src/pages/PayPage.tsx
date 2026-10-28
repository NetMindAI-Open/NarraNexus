/**
 * @file PayPage.tsx
 * @author NarraNexus
 * @date 2026-07-31
 * @description `/pay` — the landing point for the marketing pricing page's CTA.
 *
 * Why a dedicated route rather than deep-linking the settings panel: the
 * pricing page is public and its CTA has to work for a logged-out visitor too,
 * so the login redirect needs somewhere stable to send them back to.
 *
 * Four ways out, and only four:
 *
 *   logged out            → ProtectedRoute sends /login?next=%2Fpay, which
 *                           returns here after sign-in
 *   no NetMind account    → /app/settings?tab=account (a pure-local session
 *                           holds no loginToken, so it cannot buy anything)
 *   already subscribed    → /app/settings?tab=account (manage, do not re-buy)
 *   otherwise             → /app/settings?tab=account&intent=buy, which opens
 *                           the rail choice
 *
 * This page mints no checkout of its own — see PayPage.tsx.md for why that
 * changed. Consequently it has no error state: the probe swallows its own
 * failure and `navigate` cannot throw, so every path ends in a redirect.
 */

import { useCallback, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';

const ACCOUNT_PAGE = '/app/settings?tab=account';

export function PayPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isPowerUser = !!useConfigStore((s) => s.netmindToken);
  // One checkout session per visit: StrictMode double-fires effects in dev,
  // and a retry re-enters run() manually — the ref keeps concurrent runs out.
  const inFlight = useRef(false);

  const run = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    // No try/catch around this any more: the probe below swallows its own
    // failure and `navigate` does not throw, so once this page stopped minting
    // checkouts there was nothing left for an outer handler to catch. The
    // error phase and its retry button went with it rather than staying as
    // unreachable UI.
    {
      // Already subscribed → manage on the account page instead of minting a
      // second checkout session. The probe is DEFENSIVE only: the real
      // invariant lives server-side (subscribe → 400 "Already subscribed").
      // A failed read therefore means "unknown", never "abort" — a P0
      // payment path must not die because a read-only status call flaked.
      try {
        const sub = await api.getSubscription();
        if (sub.success && sub.data?.subscription?.status === 'ACTIVE') {
          navigate(ACCOUNT_PAGE, { replace: true });
          inFlight.current = false;
          return;
        }
      } catch {
        // Unknown subscription state — let subscribe() and the backend decide.
      }
      // Hand the rail choice to the account panel instead of minting a card
      // checkout here. This page is where the marketing pricing page lands, and
      // going straight to Stripe's card form made it a dead end for exactly the
      // people this work is for: Alipay and WeChat cannot pay a Stripe
      // subscription at all, so they arrived somewhere they could not buy.
      //
      // A redirect rather than a second rail picker: "which rail, how many
      // months" carries real rules (a card takes no month count, one-time is
      // bounded 1-12, a live one-time withdraws the card option), and a second
      // implementation of them would drift from the first. `intent=buy` opens
      // the panel's dialog straight away, so the CTA still lands on a purchase.
      navigate(`${ACCOUNT_PAGE}&intent=buy`, { replace: true });
    }
    inFlight.current = false;
  }, [navigate]);

  useEffect(() => {
    // Pure-local sessions hold no NetMind loginToken, so subscribe() cannot
    // authenticate — the account page explains what this session is instead.
    if (!isPowerUser) {
      navigate(ACCOUNT_PAGE, { replace: true });
      return;
    }
    void run();
  }, [isPowerUser, navigate, run]);

  // A spinner and nothing else. There is no error branch left to render: the
  // probe swallows its own failure and `navigate` cannot throw, so every path
  // through this page ends in a redirect. Keeping the old error card + retry
  // button would have been UI for a state that can no longer occur.
  return (
    <main
      className="min-h-screen flex flex-col items-center justify-center gap-4 bg-[var(--bg-primary)] px-6"
      aria-busy
    >
      <div
        className="w-8 h-8 rounded-full border-2 border-[var(--accent-primary)] border-t-transparent animate-spin"
        role="status"
        aria-label={t('pages.pay.working', 'Taking you to checkout…')}
      />
      <p className="text-sm text-[var(--text-secondary)]">
        {t('pages.pay.working', 'Taking you to checkout…')}
      </p>
    </main>
  );
}

export default PayPage;
