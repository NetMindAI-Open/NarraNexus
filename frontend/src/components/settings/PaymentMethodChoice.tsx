/**
 * @file PaymentMethodChoice.tsx
 * @author NarraNexus
 * @date 2026-08-18
 * @description The card / Alipay / WeChat selector shared by the top-up and
 * subscribe flows (nexus Stripe account).
 *
 * Why a real radiogroup and not three buttons: this is a mutually-exclusive
 * choice, so arrow keys must move between the options and only the selected
 * one belongs in the tab order. Three <button>s look identical and behave
 * wrong for anyone not using a mouse.
 *
 * Why the card option's VALUE is a prop: the two flows spell that rail
 * differently upstream — top-up calls it `default`, subscribe calls it
 * `stripe` — while Alipay and WeChat are spelled the same in both. Passing the
 * one differing value in beats duplicating the whole option list per call site.
 *
 * We never hide a rail by region: which payment method someone can actually
 * use is their business, and a US-issued card in China (or an Alipay account
 * abroad) is not an edge case worth silently removing an option for.
 *
 * `hideCard` is the one exception, and it is a CAPABILITY fact rather than a
 * preference: while a one-time (Alipay/WeChat) subscription is live, upstream
 * rejects a card subscribe with "Already subscribed to Pro." (measured on dev
 * 2026-08-19), so the renewal dialog would otherwise be offering an option
 * that cannot succeed. Do not reach for it for any softer reason.
 */

import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

type Rail = 'card' | 'alipay' | 'wechat';

// `cardValue` and `hideCard` are mutually exclusive by construction, not by
// convention: a caller that hides the card rail has no card value to name, and
// requiring one anyway forced a cast at the only such call site — a cast that
// asserts something false about the caller's own union.
type PaymentMethodChoiceProps<T extends string> = {
  /** Currently selected value, in the caller's own vocabulary. */
  value: T;
  onChange: (next: T) => void;
  disabled?: boolean;
  /** Accessible name for this group. Required in practice wherever two of these
   *  can share a screen: both announcing "Payment method" leaves a screen-reader
   *  user unable to tell which one spends which money, and a sighted user free
   *  to set one to WeChat and assume the other followed — then be charged USD. */
  label?: string;
} & (
  | {
      /** Drop the card rail — ONLY where upstream cannot accept it. See the header. */
      hideCard: true;
      cardValue?: never;
    }
  | {
      hideCard?: false;
      /** What the caller calls the card rail ('default' for top-up, 'stripe' for subscribe). */
      cardValue: T;
    }
);

function CardGlyph() {
  return (
    <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="1" y="3.5" width="14" height="9.5" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
      <path d="M1 6.8h14" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function AlipayGlyph() {
  return (
    <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="1.2" y="1.2" width="13.6" height="13.6" rx="3" stroke="currentColor" strokeWidth="1.3" />
      <path d="M3.6 10.4c3.2 1.6 6.4 1.1 8.8-1.6M5.2 4.6h5.6M8 4.6v4.2"
        stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function WeChatGlyph() {
  return (
    <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <ellipse cx="6.2" cy="6.4" rx="5" ry="4.2" stroke="currentColor" strokeWidth="1.3" />
      <ellipse cx="10.6" cy="10.2" rx="4.2" ry="3.6" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="4.8" cy="5.6" r=".75" fill="currentColor" />
      <circle cx="7.6" cy="5.6" r=".75" fill="currentColor" />
    </svg>
  );
}

// `React.JSX.Element`, not the bare global `JSX`: @types/react 19 dropped the
// global namespace and keeps only the one under React (verified — nothing in
// node_modules declares `JSX` globally any more). The bare form still passes
// `tsc --noEmit -p .` but fails `tsc -b`, which is what `npm run build` runs,
// so it would have broken the cloud image and the DMG while every local gate
// stayed green. Same shape as `React.KeyboardEvent` below, and needs no import.
const GLYPHS: Record<Rail, () => React.JSX.Element> = {
  card: CardGlyph,
  alipay: AlipayGlyph,
  wechat: WeChatGlyph,
};

export function PaymentMethodChoice<T extends string>({
  value,
  cardValue,
  onChange,
  disabled = false,
  hideCard = false,
  label,
}: PaymentMethodChoiceProps<T>) {
  const { t } = useTranslation();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const options: { rail: Rail; value: T; label: string }[] = [
    ...(hideCard || cardValue === undefined
      ? []
      : [{ rail: 'card' as Rail, value: cardValue, label: t('settings.netmind.payCard', 'Card') }]),
    { rail: 'alipay', value: 'alipay' as T, label: t('settings.netmind.payAlipay', 'Alipay') },
    { rail: 'wechat', value: 'wechat' as T, label: t('settings.netmind.payWechat', 'WeChat Pay') },
  ];

  const selected = options.findIndex((o) => o.value === value);

  // Arrow keys move the selection AND focus, which is what makes this a
  // radiogroup rather than a row of buttons wearing radio roles.
  const onKeyDown = (e: React.KeyboardEvent) => {
    const delta = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1
      : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1
      : 0;
    if (!delta) return;
    e.preventDefault();
    const next = (selected + delta + options.length) % options.length;
    onChange(options[next].value);
    refs.current[next]?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label={label ?? t('settings.netmind.payMethodLabel', 'Payment method')}
      className="flex gap-1.5"
      onKeyDown={onKeyDown}
    >
      {options.map((o, i) => {
        const active = o.value === value;
        const Glyph = GLYPHS[o.rail];
        return (
          <button
            key={o.rail}
            ref={(el) => { refs.current[i] = el; }}
            type="button"
            role="radio"
            aria-checked={active}
            // Roving tabindex: one stop for the whole group, as a native radio
            // set behaves. Falls back to the first option when nothing matches.
            tabIndex={active || (selected === -1 && i === 0) ? 0 : -1}
            disabled={disabled}
            onClick={() => onChange(o.value)}
            className={`flex-1 h-[34px] px-2 rounded-[var(--radius-sm)] border text-[13px]
              flex items-center justify-center gap-1.5 transition-colors
              disabled:opacity-50 disabled:cursor-not-allowed
              focus-visible:outline focus-visible:outline-2
              focus-visible:outline-[var(--nm-ink)] focus-visible:outline-offset-2 ${
              active
                ? 'border-[var(--nm-ink)] text-[var(--text-primary)] bg-[var(--accent-primary)]/8 font-semibold'
                : 'border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--border-strong)]'
            }`}
          >
            <Glyph />
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
