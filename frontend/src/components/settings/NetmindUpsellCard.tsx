/**
 * @file NetmindUpsellCard.tsx
 * @author NetMind.AI
 * @date 2026-07-10
 * @description The Pro plan card. Two modes:
 *   - upsell (default): shown at the decision moment (Free user whose free
 *     tier is used up), leading with the perks that differentiate Pro from a
 *     same-priced top-up, with an "Upgrade to Pro" CTA.
 *   - subscribed: the SAME plan intro inside a Pro user's manage dialog —
 *     the CTA is replaced by a "Subscribed" state chip, so the user can see
 *     what their plan includes right where they'd cancel it.
 * Price/grant/period come from GET /api/billing/plans (no hardcoding; price
 * is shown from monthly_grant_usd per product decision A). Presentational.
 */

import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui';
import { money, formatPeriod } from './netmindFormat';
import type { SubscriptionPlan } from '@/types';

interface NetmindUpsellCardProps {
  proPlan: SubscriptionPlan | null;
  onUpgrade: () => void;
  busy: boolean;
  /** Render as the current plan (state chip instead of the upgrade CTA). */
  subscribed?: boolean;
}

export function NetmindUpsellCard({ proPlan, onUpgrade, busy, subscribed = false }: NetmindUpsellCardProps) {
  const { t } = useTranslation();
  const period = formatPeriod(proPlan?.prices?.[0]?.period, t('settings.netmind.perMonth', 'mo'));
  const priceText =
    proPlan?.monthly_grant_usd != null
      ? t('settings.netmind.grantPerPeriod', '{{amount}} / {{period}}', {
          amount: `$${money(proPlan.monthly_grant_usd)}`,
          period,
        })
      : null;
  // member_price gates the headline discount perk; show it unless the plan
  // explicitly says otherwise.
  const hasMemberPrice = proPlan?.features?.member_price !== false;

  return (
    <div className="rounded-md border border-[var(--border-default)] p-3.5 space-y-3 bg-[var(--bg-sunken)]">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-semibold text-[var(--text-primary)] flex items-center gap-2">
          {t('settings.netmind.upsellCardName', 'NetMind Pro')}
          {subscribed && (
            <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-[var(--accent-primary)]/12 text-[var(--accent-primary)]">
              ✓ {t('settings.netmind.subscribedBadge', 'Subscribed')}
            </span>
          )}
        </span>
        {priceText && (
          <span className="text-xs text-[var(--text-secondary)] tabular-nums">{priceText}</span>
        )}
      </div>

      {/* Real plan value (per product): the discount + zero-fee are the true
          differentiators vs a same-priced top-up; the monthly credit grant
          (≈ the price) is fine print. Copy is fixed marketing text, not from the
          API — update these keys if NetMind changes the Pro plan. */}
      <ul className="space-y-1.5">
        {hasMemberPrice && (
          <li className="flex gap-2 text-sm text-[var(--text-primary)]">
            <span aria-hidden className="text-[var(--accent-primary)] font-semibold">✦</span>
            <span className="font-medium">
              {t('settings.netmind.upsellPerkDiscount',
                'Up to 50% off on models like OpenAI & Anthropic')}
            </span>
          </li>
        )}
        <li className="flex gap-2 text-sm text-[var(--text-primary)]">
          <span aria-hidden className="text-[var(--accent-primary)] font-semibold">✦</span>
          <span>{t('settings.netmind.upsellPerkNoFee', 'No platform service fee')}</span>
        </li>
        {/* The monthly grant, in USD (never "credits" — that concept isn't used
            anywhere in the product). Amount is the plan's monthly_grant_usd, so
            it tracks the API, not a hardcoded number. Fine print: it ≈ the price,
            so it's a reassurance ("your fee becomes usable balance"), not the
            headline. */}
        {proPlan?.monthly_grant_usd != null && (
          <li className="flex gap-2 text-xs text-[var(--text-tertiary)]">
            <span aria-hidden className="opacity-40">·</span>
            <span>
              {t('settings.netmind.upsellPerkGrant', 'Includes {{amount}} / month in usable balance', {
                amount: `$${money(proPlan.monthly_grant_usd)}`,
              })}
            </span>
          </li>
        )}
      </ul>

      {/* Subscribed mode: the plan intro is informational — no CTA (cancel
          lives next to the card in the manage dialog, upgrading is moot). */}
      {!subscribed && (
        <Button variant="accent" size="sm" onClick={onUpgrade} disabled={busy} className="w-full">
          {busy
            ? t('settings.netmind.working', 'Working…')
            : t('settings.netmind.upsellName', 'Upgrade to Pro')}
        </Button>
      )}
    </div>
  );
}
