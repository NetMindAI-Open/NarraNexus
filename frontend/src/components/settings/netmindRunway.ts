/**
 * @file netmindRunway.ts
 * @author NetMind.AI
 * @date 2026-07-10
 * @description Pure "runway health" classifier for the Account & Subscription
 * panel.
 *
 * The panel has two orthogonal dimensions: plan state (Free / Pro / Pro-ending)
 * and runway health. This file owns the latter. Runway decides whether the
 * panel stays calm ("healthy" — reassurance only, no spend button) or promotes
 * one contextual action ("low" — Free→upsell Pro, Pro→top-up). Kept pure and
 * standalone so it can be unit-tested without mounting the component.
 *
 * Charging waterfall (authoritative order is backend/NetMind — see plan #1):
 * platform free tier → subscription monthly grant → recharge balance. While the
 * free tier is still active the user can keep working, so that alone is healthy;
 * once it's gone, only a balance buffer keeps them out of the "low" state.
 */

import type { QuotaMeResponse, FeeInfo } from '@/types';

// Minimum spendable balance (USD) that still counts as "healthy" once the free
// tier is gone. Below this we promote an action. Tunable — pending product input.
export const LOW_BALANCE_USD = 1.0;

export type Runway = 'healthy' | 'low';

/**
 * Classify the account's runway.
 *
 * @param quota GET /api/quota/me result (null if the fetch failed)
 * @param fee   GET /api/billing/fee-info result (null if the fetch failed)
 * @returns 'healthy' when the user can keep working without acting now;
 *          'low' when we should surface a single contextual action.
 */
export function deriveRunway(
  quota: QuotaMeResponse | null,
  fee: FeeInfo | null,
): Runway {
  // Free tier still has budget → healthy. Arrears / ineligibility only block
  // PAID usage, so they surface as warnings elsewhere; they don't force an
  // action while the free tier can still carry the user.
  if (quota && quota.enabled === true && quota.status === 'active') {
    return 'healthy';
  }

  // Free tier is off / uninitialized / exhausted. Hard blockers → act now.
  if (fee?.checks?.has_arrears) return 'low';
  if (fee?.eligible === false) return 'low';

  // Otherwise a balance buffer is what keeps them healthy. Number() (not
  // parseFloat) so a malformed string → NaN → low, never silently over-credited.
  const balance = Number(fee?.metrics?.free_credit);
  return Number.isFinite(balance) && balance >= LOW_BALANCE_USD ? 'healthy' : 'low';
}
