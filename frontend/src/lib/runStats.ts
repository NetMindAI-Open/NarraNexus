/**
 * @file runStats.ts
 * @author NarraNexus
 * @date 2026-08-28
 * @description Non-component helpers behind the run-stats chip row: duration
 * formatting, plus the display predicate for each chip ("is there a price
 * worth showing", "are there tokens", "is there a row at all").
 *
 * Split out of components/chat/RunStatChips.tsx so that file exports
 * components only (react-refresh/only-export-components).
 *
 * The boundary against `lib/tokenFormat` is WHO CONSUMES IT, not what kind of
 * rule it is. Rules with consumers beyond this row — `formatTokens`,
 * `formatCost`, `shortModelName`, shared with the cost popover and the account
 * usage panel — live in `tokenFormat` and stay one SSOT. Rules that serve only
 * the chip row live here: every chip's display predicate, and `formatDuration`,
 * which IS pure formatting but has no second consumer anywhere.
 *
 * Two earlier attempts at this sentence drew the line in the wrong place —
 * "token/USD rules don't live here" (falsified by `hasCostToShow`) and
 * "formatting vs display-worthiness" (falsified by `formatDuration`, right
 * below). Both times the file's own contents were the counterexample.
 */

import { inputSideTokens } from './tokenFormat';
import type { EventLogMeta } from '@/types/api';

/** 90 → "1m 30s", 42 → "42s", 3900 → "1h 5m". */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
}

/**
 * Is there a price worth showing?
 *
 * `> 0`, not `!= null`. A booked 0 is not a cheap turn — it is an UNPRICED
 * one: `model_pricing.price_for` returns None for any model id litellm's
 * table doesn't know, `calculate_cost` then books 0, and that is the majority
 * of rows on a local install (1837 of 2384 when this was written, all of them
 * the main DeepSeek / GLM ids). Gating on `!= null` sent that 0 into
 * `formatCost`, whose own contract says callers gate on > 0, and it renders
 * sub-hundredth-of-a-cent values as "<$0.0001" — so "we don't know the rate"
 * appeared on screen as "it cost a little something".
 *
 * Same rule as the backend's `_build_event_meta`, which leaves
 * `total_cost_usd` None when there are no ledger rows precisely so the UI can
 * hide the chip instead of showing a misleading $0. No rows and no price are
 * the same situation to a reader.
 *
 * A type predicate, not a plain boolean: it lets the caller pass
 * `meta.total_cost_usd` straight to `formatCost` without an `as number`. An
 * assertion there would silently survive someone loosening this gate back to
 * `!= null` — and `formatCost(null)` walks right back into the bug above.
 */
export function hasCostToShow(
  meta: EventLogMeta,
): meta is EventLogMeta & { total_cost_usd: number } {
  return meta.total_cost_usd != null && meta.total_cost_usd > 0;
}

/** Any token movement at all, cache buckets included. */
export function hasTokens(meta: EventLogMeta): boolean {
  return inputSideTokens(meta) > 0 || meta.output_tokens > 0;
}

/**
 * Would the chip row render anything?
 *
 * Exported so a caller can collapse its whole container up front rather than
 * leave an empty strip around a component that returned null. BOTH callers
 * must use it: RunStatChips returning null still leaves the caller's wrapper
 * div behind, margin and all.
 *
 * Predicate and render must agree. They are two hand-written copies of the
 * same conditions today; the shared helpers (`hasCostToShow`, `hasTokens`)
 * exist so the ones that have already been gotten wrong stay in one place.
 * Adding a chip means touching both — a mismatch shows up as a turn whose
 * only datum is the new chip rendering no row at all.
 */
export function hasRunStats(meta: EventLogMeta): boolean {
  return (
    meta.state === 'failed' ||
    meta.state === 'cancelled' ||
    meta.duration_seconds != null ||
    hasCostToShow(meta) ||
    hasTokens(meta) ||
    meta.models.length > 0
  );
}
