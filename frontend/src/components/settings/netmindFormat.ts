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

// A single "how much free tier is left" percentage (0–100), taking the more
// depleted of input/output — that's the honest ceiling on what you can still
// do. Returns null when there is no free-tier bar to show (feature off /
// uninitialized / disabled).
export function freeTierPctLeft(quota: QuotaMeResponse | null): number | null {
  if (!quota || quota.enabled !== true) return null;
  if (quota.status === 'exhausted') return 0;
  if (quota.status !== 'active') return null; // uninitialized / disabled
  const totIn = quota.initial_input_tokens + quota.granted_input_tokens;
  const totOut = quota.initial_output_tokens + quota.granted_output_tokens;
  const rIn = totIn > 0 ? quota.remaining_input_tokens / totIn : 1;
  const rOut = totOut > 0 ? quota.remaining_output_tokens / totOut : 1;
  return Math.max(0, Math.min(100, Math.floor(Math.min(rIn, rOut) * 100)));
}

// Compact token count: 4_500_000 → "4.5M", 900_000 → "900K", 850 → "850".
// One decimal max, trailing ".0" trimmed — panel row values, not analytics.
export function formatTokens(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '0';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, '')}K`;
  return String(Math.floor(n));
}

// Remaining/total tokens of the MORE DEPLETED dimension (same dimension the
// pct bar reflects, so the number and the bar never disagree). Returns null
// exactly when freeTierPctLeft would (feature off / uninitialized / disabled);
// exhausted is the caller's business (the bar collapses to a note there).
export function freeTierTokensLeft(
  quota: QuotaMeResponse | null,
): { remaining: number; total: number } | null {
  if (!quota || quota.enabled !== true) return null;
  if (quota.status !== 'active' && quota.status !== 'exhausted') return null;
  const totIn = quota.initial_input_tokens + quota.granted_input_tokens;
  const totOut = quota.initial_output_tokens + quota.granted_output_tokens;
  const rIn = totIn > 0 ? quota.remaining_input_tokens / totIn : 1;
  const rOut = totOut > 0 ? quota.remaining_output_tokens / totOut : 1;
  return rIn <= rOut
    ? { remaining: Math.max(0, quota.remaining_input_tokens), total: totIn }
    : { remaining: Math.max(0, quota.remaining_output_tokens), total: totOut };
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
