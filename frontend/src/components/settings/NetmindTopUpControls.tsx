/**
 * @file NetmindTopUpControls.tsx
 * @author NetMind.AI
 * @date 2026-07-10
 * @description Top-up ("Add credits") controls for the Account & Subscription
 * panel: payment method + preset tiers + custom amount + Recharge button +
 * the processing/success/failed feedback row (with the "Stop waiting" escape).
 * Purely presentational — state and handlers (double-submit guard, poll
 * generation, Stripe kickoff, the exchange-rate quote and its minimum) live in
 * NetmindAccountPanel.
 *
 * The CNY block renders only for WeChat, and only from a quote the panel has
 * already matched to the current amount — this component never decides whether
 * a quote is fresh, it just draws the one it is handed.
 */

import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui';
import { money, moneyOrNull } from './netmindFormat';
import { PaymentMethodChoice } from './PaymentMethodChoice';
import type { FxQuote, RechargePaymentMethod } from '@/types';

// Preset top-up tiers (USD). The API accepts any positive amount; these are a
// NarraNexus-side convenience (module E / D-5). A custom amount overrides them.
const RECHARGE_TIERS = [5, 10, 20, 50];

export type RechargeState = 'idle' | 'processing' | 'success' | 'failed';

interface NetmindTopUpControlsProps {
  tier: number;
  custom: string;
  /** This top-up's own progress — drives the NARRATIVE only. */
  rechargeState: RechargeState;
  /** Any money action is in flight, whichever control started it — see the twin
   *  prop on NetmindProPurchase for why this is not `rechargeState`. */
  busy: boolean;
  rechargeError: string | null;
  paymentMethod: RechargePaymentMethod;
  /** Quote for the CURRENT amount, or null while one is pending / not needed.
   *  The panel clears it the moment the amount changes, so the two numbers
   *  rendered here can never disagree with each other. */
  fx: FxQuote | null;
  fxLoading: boolean;
  onChangePaymentMethod: (m: RechargePaymentMethod) => void;
  onSelectTier: (tier: number) => void;
  onChangeCustom: (value: string) => void;
  onRecharge: () => void;
  onStopWaiting: () => void;
}

export function NetmindTopUpControls({
  tier,
  custom,
  rechargeState,
  busy,
  rechargeError,
  paymentMethod,
  fx,
  fxLoading,
  onChangePaymentMethod,
  onSelectTier,
  onChangeCustom,
  onRecharge,
  onStopWaiting,
}: NetmindTopUpControlsProps) {
  const { t } = useTranslation();
  // Upstream returns money as a high-precision decimal STRING ("67.531480",
  // "5.0" — measured against dev 2026-08-19), not something display-ready.
  // `moneyOrNull` (shared, guarded) rather than a local Number().toFixed():
  // every FxQuote field is optional, so an unguarded conversion renders "¥NaN"
  // on the line someone reads last before paying.
  // `rate` is deliberately NOT rounded: an exchange rate's precision is
  // information, whereas a price with six decimals is just noise.
  const cny = moneyOrNull;

  const narrating = rechargeState === 'processing';
  const isWechat = paymentMethod === 'wechat';
  // The converted total goes ON the button as well as in the note: the last
  // thing someone reads before committing should be the number their bank will
  // actually take, not the one they typed.
  const chargeLabel = isWechat && cny(fx?.charge_amount) ? `¥${cny(fx?.charge_amount)}` : '';

  return (
    <div className="space-y-2">
      <div className="text-sm font-medium text-[var(--text-primary)]">
        {t('settings.netmind.rechargeTitle', 'Add credits')}
      </div>
      <p className="text-xs text-[var(--text-tertiary)]">
        {t('settings.netmind.rechargeDesc',
          'One-time top-up, no subscription. Credits are kept regardless of plan.')}
      </p>
      <PaymentMethodChoice
        value={paymentMethod}
        cardValue="default"
        label={t('settings.netmind.payMethodLabelTopUp', 'How to pay for these credits')}
        onChange={onChangePaymentMethod}
        disabled={busy}
      />
      <div className="flex flex-wrap items-center gap-1.5">
        {RECHARGE_TIERS.map((v) => {
          const active = !custom.trim() && tier === v;
          return (
            <button
              key={v}
              type="button"
              onClick={() => onSelectTier(v)}
              disabled={busy}
              className={`px-3 py-1 rounded-[var(--radius-md)] text-sm border transition-colors disabled:opacity-50 ${
                active
                  ? 'border-[var(--accent-primary)] text-[var(--accent-primary)] bg-[var(--accent-primary)]/8'
                  : 'border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--border-strong)]'
              }`}
            >
              ${v}
            </button>
          );
        })}
        <div className="flex items-center gap-1 ml-1">
          <span className="text-sm text-[var(--text-tertiary)]">$</span>
          <input
            type="number"
            min="1"
            inputMode="decimal"
            value={custom}
            onChange={(e) => onChangeCustom(e.target.value)}
            placeholder={t('settings.netmind.rechargeCustom', 'Custom')}
            disabled={busy}
            className="w-24 px-2 py-1 rounded-[var(--radius-md)] text-sm bg-[var(--bg-primary)] border border-[var(--border-default)] text-[var(--text-primary)] disabled:opacity-50"
          />
        </div>
        <Button variant="accent" size="sm" onClick={onRecharge} disabled={busy}>
          {narrating
            ? t('settings.netmind.working', 'Working…')
            : chargeLabel
              ? `${t('settings.netmind.rechargeBtn', 'Recharge')} ${chargeLabel}`
              : t('settings.netmind.rechargeBtn', 'Recharge')}
        </Button>
      </div>
      {isWechat && (fx || fxLoading) && (
        <div className="space-y-1 pt-0.5 border-t border-[var(--nm-hairline)]">
          {fx?.charge_amount && fx?.amount_usd ? (
            <p className="text-xs text-[var(--text-secondary)] tabular-nums">
              {t('settings.netmind.wechatConversion',
                'WeChat is charged in CNY: {{usd}} ≈ {{cny}}',
                { usd: `$${money(fx.amount_usd)}`, cny: `¥${cny(fx.charge_amount)}` })}
              {fx.rate && (
                <span className="ml-2 text-[var(--text-tertiary)]">1 USD = {fx.rate} CNY</span>
              )}
            </p>
          ) : (
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.wechatConverting', 'Converting…')}
            </p>
          )}
          <p className="text-[11px] text-[var(--text-tertiary)]">
            {t('settings.netmind.wechatNote',
              'You still receive the USD amount as credit. The rate is applied when payment starts.')}
            {fx?.min_charge && ` ${t('settings.netmind.wechatMin',
              'Minimum {{min}} per payment.', { min: `¥${cny(fx.min_charge)}` })}`}
          </p>
        </div>
      )}
      {narrating && (
        <div className="flex items-start justify-between gap-3">
          <p className="text-xs text-[var(--text-tertiary)] flex-1">
            {t('settings.netmind.rechargeProcessing',
              'Waiting for payment… complete it in the opened window; your balance updates automatically.')}
          </p>
          <button
            type="button"
            onClick={onStopWaiting}
            className="shrink-0 text-xs text-[var(--text-secondary)] underline underline-offset-2 hover:text-[var(--text-primary)]"
          >
            {t('settings.netmind.rechargeStopWaiting', 'Stop waiting')}
          </button>
        </div>
      )}
      {rechargeState === 'success' && (
        <p className="text-xs text-[var(--color-success)]">
          {t('settings.netmind.rechargeSuccess', 'Top-up complete — balance updated.')}
        </p>
      )}
      {rechargeState === 'failed' && rechargeError && (
        <p className="text-xs text-[var(--color-error)]">{rechargeError}</p>
      )}
    </div>
  );
}
