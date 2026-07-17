/**
 * @file NetmindTopUpControls.tsx
 * @author NetMind.AI
 * @date 2026-07-10
 * @description Top-up ("Add credits") controls for the Account & Subscription
 * panel: preset tiers + custom amount + Recharge button + the
 * processing/success/failed feedback row (with the "Stop waiting" escape).
 * Purely presentational — state and handlers (double-submit guard, poll
 * generation, Stripe kickoff) live in NetmindAccountPanel.
 */

import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui';

// Preset top-up tiers (USD). The API accepts any positive amount; these are a
// NarraNexus-side convenience (module E / D-5). A custom amount overrides them.
const RECHARGE_TIERS = [5, 10, 20, 50];

export type RechargeState = 'idle' | 'processing' | 'success' | 'failed';

interface NetmindTopUpControlsProps {
  tier: number;
  custom: string;
  rechargeState: RechargeState;
  rechargeError: string | null;
  onSelectTier: (tier: number) => void;
  onChangeCustom: (value: string) => void;
  onRecharge: () => void;
  onStopWaiting: () => void;
}

export function NetmindTopUpControls({
  tier,
  custom,
  rechargeState,
  rechargeError,
  onSelectTier,
  onChangeCustom,
  onRecharge,
  onStopWaiting,
}: NetmindTopUpControlsProps) {
  const { t } = useTranslation();
  const processing = rechargeState === 'processing';

  return (
    <div className="space-y-2">
      <div className="text-sm font-medium text-[var(--text-primary)]">
        {t('settings.netmind.rechargeTitle', 'Add credits')}
      </div>
      <p className="text-xs text-[var(--text-tertiary)]">
        {t('settings.netmind.rechargeDesc',
          'One-time top-up, no subscription. Credits are kept regardless of plan.')}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        {RECHARGE_TIERS.map((v) => {
          const active = !custom.trim() && tier === v;
          return (
            <button
              key={v}
              type="button"
              onClick={() => onSelectTier(v)}
              disabled={processing}
              className={`px-3 py-1 rounded-md text-sm border transition-colors disabled:opacity-50 ${
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
            disabled={processing}
            className="w-24 px-2 py-1 rounded-md text-sm bg-[var(--bg-primary)] border border-[var(--border-default)] text-[var(--text-primary)] disabled:opacity-50"
          />
        </div>
        <Button variant="accent" size="sm" onClick={onRecharge} disabled={processing}>
          {processing
            ? t('settings.netmind.working', 'Working…')
            : t('settings.netmind.rechargeBtn', 'Recharge')}
        </Button>
      </div>
      {processing && (
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
