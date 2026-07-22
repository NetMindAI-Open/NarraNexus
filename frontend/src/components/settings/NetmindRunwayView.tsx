/**
 * @file NetmindRunwayView.tsx
 * @author NetMind.AI
 * @date 2026-07-10
 * @description "Runway" breakdown for the Account & Subscription panel: the
 * pools BELOW the balance hero — platform free tier (bar) + (Pro) monthly grant
 * (shown as "included in balance", NOT an additive number) — plus the
 * charging-order line. Free-tier-first is platform behavior (the old
 * prefer_system toggle was removed 2026-07-18), so there is nothing to
 * switch here. The free tier is a one-time grant with no periodic refresh,
 * so once used up the bar collapses to a single explanatory line
 * (freeTierExhausted) instead of a permanent 0% warning bar. The spendable
 * balance itself is the hero in the panel, not here. Purely presentational;
 * the panel owns the data.
 */

import { useTranslation } from 'react-i18next';

interface NetmindRunwayViewProps {
  /** Free-tier % remaining (1–100), or null when there is no free-tier bar
   *  (feature off, no quota row, used up — or replaced by the plan-credit
   *  bar for a Pro account with the subscription split active). Drives the
   *  bar WIDTH; the row VALUE is freeTokensText. */
  freePct: number | null;
  /** Pre-formatted row value ("3.9M / 4.5M tokens left") — same more-depleted
   *  dimension as freePct so the number and the bar never disagree. */
  freeTokensText: string | null;
  /** Pre-formatted monthly grant (e.g. "$19.00 / mo") — legacy line for Pro
   *  accounts on an API without subscription_credit; null otherwise. */
  grantText: string | null;
  /** Free tier used up: no bar (freePct is null), one muted note instead. */
  freeTierExhausted: boolean;
  /** This cycle's plan-credit % remaining (0–100), or null when the
   *  subscription split isn't active. Unlike the free tier, 0 keeps the
   *  bar (plus a "refreshes next cycle" note) — the tank refills. */
  subPct: number | null;
  /** Pro users get the three-pool flow line; Free users the two-pool one. */
  flowIsPro: boolean;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="text-[var(--text-secondary)]">{label}</span>
      <span className="tabular-nums text-[var(--text-primary)]">{value}</span>
    </div>
  );
}

export function NetmindRunwayView({
  freePct,
  freeTokensText,
  grantText,
  freeTierExhausted,
  subPct,
  flowIsPro,
}: NetmindRunwayViewProps) {
  const { t } = useTranslation();
  // Flow line only makes sense with ≥2 pools (free tier / grant / plan credit,
  // alongside the balance hero). A single balance pool → hide it (#3).
  const showFlow = freePct !== null || !!grantText || subPct !== null;

  return (
    <div className="space-y-2.5">
      {freePct !== null && (
        <div className="space-y-1.5">
          <Row
            label={t('settings.netmind.freeTierLabel', 'Free tier')}
            value={
              freeTokensText ??
              t('settings.netmind.freeTierLeft', '{{pct}}% left', { pct: freePct })
            }
          />
          <div
            role="progressbar"
            aria-valuenow={freePct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={t('settings.netmind.freeTierLabel', 'Free tier')}
            className="h-1.5 rounded-full bg-[var(--bg-sunken)] overflow-hidden"
          >
            <div
              className="h-full rounded-full"
              style={{
                width: `${freePct}%`,
                backgroundColor: 'var(--accent-primary)',
              }}
            />
          </div>
        </div>
      )}

      {/* One-time grant, no refresh: once used up, a permanent 0% warning bar
          is dead weight — one quiet line keeps the billing story honest
          ("why is my balance being charged now") without alarm styling. */}
      {freeTierExhausted && (
        <p className="text-xs text-[var(--text-tertiary)]">
          {t('settings.netmind.freeTierExhaustedNote',
            'Free tier used up — usage now draws from your balance.')}
        </p>
      )}

      {/* Plan-credit bar (the "overflow tank"): this cycle's grant refills it
          to 100% every period; older cycles' leftover already sits in the
          balance hero. Unlike the free tier, 0% KEEPS the bar — the tank
          refills next cycle, so a quiet note beats collapsing it. */}
      {subPct !== null && (
        <div className="space-y-1.5">
          <Row
            label={t('settings.netmind.subCreditLabel', 'Plan credit')}
            value={t('settings.netmind.freeTierLeft', '{{pct}}% left', { pct: subPct })}
          />
          <div
            role="progressbar"
            aria-valuenow={subPct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={t('settings.netmind.subCreditLabel', 'Plan credit')}
            className="h-1.5 rounded-full bg-[var(--bg-sunken)] overflow-hidden"
          >
            <div
              className="h-full rounded-full"
              style={{ width: `${subPct}%`, backgroundColor: 'var(--accent-primary)' }}
            />
          </div>
          {subPct === 0 && (
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.subCycleExhaustedNote',
                "This cycle's plan credit is used up — it refreshes next cycle.")}
            </p>
          )}
        </div>
      )}

      {/* Grant is NOT a separate spendable number — the API folds it into the
          balance hero (free_credit). Show it as an informational "included"
          line so the user never adds it on top of the hero. */}
      {grantText && (
        <div className="flex items-center justify-between gap-3 text-sm">
          <span className="text-[var(--text-secondary)]">
            {t('settings.netmind.monthlyGrant', 'Monthly grant')}{' '}
            <span className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.grantIncluded', '(included in balance)')}
            </span>
          </span>
          <span className="tabular-nums text-[var(--text-secondary)]">{grantText}</span>
        </div>
      )}

      {/* Charging-order line — only with ≥2 pools; never claim "free tier first"
          when there is no free-tier bar on screen. */}
      {showFlow && (
        <p className="text-xs text-[var(--text-tertiary)]">
          {subPct !== null
            ? t('settings.netmind.flowProSub',
                'Usage draws your plan credit first, then your balance.')
            : freePct !== null
              ? flowIsPro
                ? t('settings.netmind.flowPro',
                    'Usage draws the free tier first, then your monthly grant, then your balance.')
                : t('settings.netmind.flowFree',
                    'Usage draws the free tier first, then your balance.')
              : t('settings.netmind.flowProNoTier',
                  'Usage draws your monthly grant first, then your balance.')}
        </p>
      )}

    </div>
  );
}
