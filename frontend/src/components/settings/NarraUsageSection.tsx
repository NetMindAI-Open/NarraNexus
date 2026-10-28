/**
 * @file NarraUsageSection.tsx
 * @author NarraNexus
 * @date 2026-08-19
 * @description "How much of this did NarraNexus actually use" — the platform's
 * own ledger, rendered next to the NetMind balance it is constantly confused
 * with.
 *
 * Why this exists
 * ---------------
 * The balance and the activity list above it come from NetMind's FINANCE
 * domain (`/v1/finance/user-fee-info`, `/v1/finance/records`), which is scoped
 * to the NETMIND ACCOUNT — every product that account touches, not just this
 * one. Spend the same account on another platform and the number in this card
 * drops, with nothing on screen saying why. Reported 2026-08-19 (P2, "usage
 * shows model/api usage, not narra usage").
 *
 * NarraNexus does keep its own per-call ledger (`cost_records`, written by
 * utils/cost_tracker.py, read via GET /api/agents/_all/costs scoped to the
 * agents the viewer owns). It was only ever surfaced in the chat header's
 * token popover — nowhere near the balance. This section closes that gap.
 *
 * The dollar figure is deliberately an ESTIMATE
 * --------------------------------------------
 * `cost_records.total_cost_usd` prices tokens at litellm's published LIST rate;
 * NetMind is an aggregator and does not invoice at the vendor's direct rate
 * (see utils/model_pricing.py, "LIST price, not invoice price"). So this number
 * answers "roughly how much did NarraNexus consume" and must never be presented
 * as reconciling against the balance above. Hence `≈` plus a stated caveat —
 * and hence tokens, which ARE measured exactly, carry the display.
 *
 * Failure posture: this is an explanatory add-on to a card about money. A
 * broken/empty ledger renders NOTHING rather than an error or a "$0.00" (which
 * reads as "free" instead of "unknown"). It must not be able to take the
 * billing card down with it.
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/api';
import {
  formatCost,
  formatTokens,
  shortModelName,
  summaryTotalTokens,
  totalTokens,
} from '@/lib/tokenFormat';
import type { CostSummary } from '@/types';

/** Match the NetMind finance view above, which is month-shaped. */
const WINDOW_DAYS = 30;
/**
 * Headroom, not a limit that bites today: the endpoint currently returns at most
 * two buckets (see `shortModelName` in lib/tokenFormat.ts). 4 leaves room for a
 * widened contract without this card silently truncating on the day it widens.
 */
const MAX_MODELS = 4;

export function NarraUsageSection() {
  const { t } = useTranslation();
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const mounted = useRef(true);

  // One effect owns both reads of the same external system: the initial one and
  // every refresh. Usage accrues while the user is elsewhere (agents run in the
  // background, and the tab that spends is rarely this one), and the rest of
  // this card already refreshes on focus for exactly that reason — a block that
  // froze at its mount-time value would be the single stale number on a screen
  // of live ones, which is worse than not showing it.
  useEffect(() => {
    mounted.current = true;
    // Synchronous in-flight guard, same shape as the panel's `pollingRef`. This
    // is the heaviest read on the settings page: the endpoint scans every
    // cost_records row in the window for every agent the viewer owns, with no
    // SQL LIMIT and the aggregation done in Python (backend/routes/agents/
    // cost.py) — so its cost grows with the account's history, and the trigger
    // is "user alt-tabbed", which they may do twice in a second. Skipping while
    // a read is already in the air also removes the out-of-order case: two
    // concurrent reads could resolve backwards and flash a stale total.
    //
    // Deliberately NOT a minimum refresh interval: that would blunt the
    // post-payment case where the user comes back from an external window and
    // must see fresh numbers immediately.
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      // `.catch(() => null)` IS the failure posture (see the file header): a
      // failed first load leaves the section absent, a failed REFRESH leaves
      // the last good value standing. Neither says anything to the user —
      // nothing here is actionable, and an error line inside a billing card
      // reads as "your money is broken". The debug line is the only trace:
      // without it, "this endpoint is 500ing" and "this account has no usage"
      // look identical from the outside, forever.
      const res = await api.getCosts('_all', WINDOW_DAYS).catch((e: unknown) => {
        console.debug('[narra-usage] cost ledger read failed', e);
        return null;
      });
      inFlight = false;
      if (!mounted.current || !res) return;
      setSummary(res.summary ?? null);
    };
    const onFocus = () => void load();
    window.addEventListener('focus', onFocus);
    void load();
    return () => {
      mounted.current = false;
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  if (!summary) return null;
  const total = summaryTotalTokens(summary);
  // No usage yet is not a fact worth a heading. It is also the state a brand
  // new account is in, where an empty "0" block is pure noise.
  if (total <= 0) return null;

  const models = Object.entries(summary.by_model ?? {})
    .sort(([, a], [, b]) => totalTokens(b) - totalTokens(a))
    .slice(0, MAX_MODELS);
  // > 0 only: an unpriced model books $0, and "$0.00" would claim it was free
  // rather than admit we don't know the rate.
  const costText = summary.total_cost_usd > 0 ? formatCost(summary.total_cost_usd) : null;

  return (
    <div className="pt-3 border-t border-[var(--border-subtle)] space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-[var(--text-secondary)]">
          {t('settings.netmind.narraUsageTitle', 'Used by NarraNexus · last {{days}} days', {
            days: WINDOW_DAYS,
          })}
        </span>
        <span className="font-mono tabular-nums text-sm text-[var(--text-primary)]">
          {formatTokens(total)}
          <span className="ml-1 text-[11px] text-[var(--text-tertiary)]">
            {t('settings.netmind.narraUsageTokens', 'tokens')}
          </span>
        </span>
      </div>

      {models.length > 0 && (
        <ul className="space-y-1">
          {models.map(([model, data]) => (
            <li
              key={model}
              data-testid="narra-usage-model"
              className="flex items-center justify-between gap-2 text-xs text-[var(--text-secondary)]"
            >
              {/* Same two i18n keys as the chat header's token popover: the
                  endpoint buckets by call_type, not by model, and the two
                  surfaces must not invent two names for the same bucket. */}
              <span className="truncate" title={model}>
                {shortModelName(model, {
                  main: t('cost.popover.modelUsage', 'Model usage'),
                  helper: t('cost.popover.helperUsage', 'Helper Model Usage'),
                })}
              </span>
              <span className="flex items-center gap-2 shrink-0">
                <span className="text-[11px] text-[var(--text-tertiary)]">
                  ×{data.call_count}
                </span>
                <span className="font-mono tabular-nums text-[var(--text-primary)] min-w-[52px] text-right">
                  {formatTokens(totalTokens(data))}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {costText && (
        <p className="text-[11px] text-[var(--text-tertiary)] leading-relaxed">
          {t(
            'settings.netmind.narraUsageEstimate',
            '≈ {{cost}} — estimated from public list prices, so it will not match your NetMind statement exactly.',
            { cost: costText },
          )}
        </p>
      )}
    </div>
  );
}
