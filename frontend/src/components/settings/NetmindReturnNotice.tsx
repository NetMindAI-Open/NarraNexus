/**
 * @file NetmindReturnNotice.tsx
 * @date 2026-07-30
 * @description The one line the Account panel shows a payer who just came back
 * from Stripe. Purely presentational; the query params behind it are consumed by
 * useNetmindPaymentReturn.
 *
 * Rendered ABOVE the panel's loading/error branches on purpose: "did my payment
 * land" is the question the user arrived with, and it must not wait on the
 * panel's fetches — nor disappear because one of them failed.
 */

import { useTranslation } from 'react-i18next';
import type { ReturnNotice } from './useNetmindPaymentReturn';

export function NetmindReturnNotice({ notice }: { notice: ReturnNotice }) {
  const { t } = useTranslation();

  // A cancelled payment is not an error — nothing went wrong, the user chose to
  // back out — so it stays in neutral ink rather than the error colour.
  const cancelled = notice.status === 'cancelled';

  const text = cancelled
    ? t('settings.netmind.returnCancelled',
        'Payment cancelled — you have not been charged.')
    : notice.flow === 'topup'
      // "received" is earned: Stripe only redirects here once the payment
      // completed. "refreshed" would NOT be — NetMind credits the account a
      // moment later, which is what the hook's settle refresh is for.
      ? t('settings.netmind.returnTopupSuccess',
          'Top-up received — updating your balance.')
      // Subscription, or an older session with no flow tag: the plan may take a
      // moment to flip upstream, so don't claim it is Pro yet.
      : t('settings.netmind.returnSubscribeSuccess',
          'Payment received — applying it to your account now.');

  return (
    <div
      role="status"
      className="px-4 py-2.5 border-b border-[var(--border-subtle)] text-xs"
      style={{ color: cancelled ? 'var(--text-secondary)' : 'var(--color-success)' }}
    >
      {text}
    </div>
  );
}
