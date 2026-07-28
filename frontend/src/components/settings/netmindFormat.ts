/**
 * @file netmindFormat.ts
 * @author NetMind.AI
 * @date 2026-07-10
 * @description Pure formatting helpers shared by the Account & Subscription
 * panel and its subcomponents (RunwayView / UpsellCard). Kept separate so the
 * presentational pieces stay dumb and unit-testable without the panel.
 */

import type { QuotaMeResponse } from '@/types';

// Money strings from NetMind can carry 4 decimals ("9.9300"); show 2.
export function money(v?: string | number | null): string {
  if (v == null || v === '') return '—';
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(2) : '—';
}

// The free-tier wallet needs more precision than a balance does. A real agent
// turn on it costs a fraction of a cent (~$0.0027), so at two decimals a whole
// session of use rounds away and the grant looks frozen at "10.00" — which is
// exactly how it read to the Owner on 2026-07-28. Six decimals is the gateway's
// own resolution, and padding keeps the digit count stable so the number does
// not visibly reflow between polls.
export function creditMoney(v?: string | number | null): string {
  if (v == null || v === '') return '—';
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(6) : '—';
}

// A single "how much free tier is left" percentage (0–100) of the wallet.
// Returns null when there is no free-tier bar to show (feature off /
// uninitialized).
export function freeTierPctLeft(quota: QuotaMeResponse | null): number | null {
  if (!quota || quota.enabled !== true) return null;
  if (quota.status === 'exhausted') return 0;
  if (quota.status !== 'active') return null; // uninitialized
  if (!(quota.max_budget > 0)) return null;
  const ratio = quota.remaining / quota.max_budget;
  return Math.max(0, Math.min(100, Math.floor(ratio * 100)));
}

// Remaining / total dollars of the free-tier wallet. Returns null exactly when
// freeTierPctLeft would for a non-exhausted wallet; exhausted is the caller's
// business (the bar collapses to a note there).
export function freeTierCreditLeft(
  quota: QuotaMeResponse | null,
): { remaining: number; total: number; currency: string } | null {
  if (!quota || quota.enabled !== true) return null;
  if (quota.status !== 'active' && quota.status !== 'exhausted') return null;
  return {
    remaining: Math.max(0, quota.remaining),
    total: quota.max_budget,
    currency: quota.currency,
  };
}

// Format a plan billing period. NetMind dev drifts period to "2day"; prod is
// "month". Map the common case to a short localized label, pass anything else
// through verbatim so an unexpected value is visible rather than hidden.
export function formatPeriod(period: string | undefined, monthLabel: string): string {
  if (!period) return monthLabel;
  return period === 'month' ? monthLabel : period;
}

export function formatDate(unixSeconds: number): string {
  try {
    return new Date(unixSeconds * 1000).toISOString().slice(0, 10);
  } catch {
    return '—';
  }
}
