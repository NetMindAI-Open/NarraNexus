/**
 * @file_name: WelcomeStepFrame.tsx
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: The right-hand pane every welcome step is poured into: back
 * link, page heading, scrolling body, and a footer pinned to the bottom with
 * one primary CTA plus a skip.
 *
 * It exists so the three steps can't disagree on the things a flow must keep
 * identical — heading scale, column width, where the primary action lives.
 * A step that moved its CTA would read as a different product.
 *
 * The heading is a `div role="heading"`, not an `<h1>`: index.css styles bare
 * h1-h6 outside any cascade layer, so an unlayered rule would beat the utility
 * class (the same trap that made every dialog title 32px — see [[Dialog]]).
 */

import { useTranslation } from 'react-i18next';
import { ArrowLeft } from 'lucide-react';
import type { ReactNode } from 'react';
import { Button } from '@/components/ui';

export interface WelcomeStepFrameProps {
  title: string;
  /** Optional supporting line. Steps that need no explanation pass nothing. */
  subtitle?: string;
  children: ReactNode;
  /** Shown above the heading when there is a previous step. */
  onBack?: () => void;
  /** Footer: primary action. Omit `onPrimary` to render no CTA at all. */
  primaryLabel?: string;
  primaryIcon?: ReactNode;
  onPrimary?: () => void;
  primaryDisabled?: boolean;
  /** Footer: the always-available escape. */
  skipLabel: string;
  onSkip: () => void;
  /** Small mono line above the CTA (selection summary, progress, …). */
  footerNote?: ReactNode;
}

export function WelcomeStepFrame({
  title,
  subtitle,
  children,
  onBack,
  primaryLabel,
  primaryIcon,
  onPrimary,
  primaryDisabled,
  skipLabel,
  onSkip,
  footerNote,
}: WelcomeStepFrameProps) {
  const { t } = useTranslation();

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[var(--nm-card)]">
      {/* Centred while the step fits, top-aligned once it doesn't (the import
          list can be 29 rows) — `my-auto` inside a flex column does both, where
          `justify-center` alone would clip the overflow. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-6 py-6 md:px-12 md:py-10">
        <div className="mx-auto my-auto w-full max-w-[560px]">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="mb-3.5 inline-flex items-center gap-1.5 text-xs text-[var(--nm-ink50)] hover:text-[var(--nm-ink)]"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              {t('common.back')}
            </button>
          )}
          <div
            role="heading"
            aria-level={1}
            className="font-[family-name:var(--font-display)] text-2xl font-bold leading-tight tracking-tight text-[var(--nm-ink)]"
          >
            {title}
          </div>
          {subtitle && (
            <p className="mt-2 text-[13px] leading-relaxed text-[var(--nm-ink70)]">{subtitle}</p>
          )}
          <div className="mt-5">{children}</div>
        </div>
      </div>

      <div className="border-t border-[var(--nm-hairline)] px-6 pb-5 pt-4 md:px-12">
        <div className="mx-auto flex max-w-[560px] flex-col gap-2.5">
          {footerNote && (
            <div className="text-center font-[family-name:var(--font-mono)] text-[11px] tabular-nums text-[var(--nm-ink50)]">
              {footerNote}
            </div>
          )}
          {onPrimary && (
            <Button
              variant="accent"
              onClick={onPrimary}
              disabled={primaryDisabled}
              className="h-11 w-full text-sm"
            >
              {primaryIcon}
              {primaryLabel}
            </Button>
          )}
          <button
            type="button"
            onClick={onSkip}
            className="h-8 text-xs text-[var(--nm-ink50)] hover:text-[var(--nm-ink)]"
          >
            {skipLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
