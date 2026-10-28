/**
 * @file_name: WelcomeRail.tsx
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: The first-run flow's left rail — brand, step tracker, and the
 * two escapes a first-run screen must always offer (language, log out).
 *
 * The rail exists so the user can always see how many screens are left; a
 * first-run flow that hides its own length reads as endless. Steps come from
 * [[welcomeSteps]], so the rail never shows a step the flow won't render.
 *
 * Surface: the marketing site's graph paper (72px cells, ink at 4% — the exact
 * spec lifted from narra.nexus's `body:before`), on L0 paper. The rail carries
 * it alone; the content pane stays a plain L1 card so forms read cleanly
 * (Owner decision 2026-08-27).
 *
 * Below md the rail collapses into a top progress strip — the DMG's smallest
 * window must be able to finish the flow (binding rule #7).
 */

import type { CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, LogOut } from 'lucide-react';
import { BetaBadge, LanguageToggle } from '@/components/ui';
import { cn } from '@/lib/utils';
import type { WelcomeStepId } from '@/lib/welcomeSteps';

/** The graph-paper overlay, lifted verbatim from narra.nexus's `body:before`
 *  (72px cells, ink at 4%). Module-private: both usages are in this file, and
 *  exporting a constant from a component file breaks fast refresh. */
const WELCOME_GRID_STYLE: CSSProperties = {
  backgroundImage:
    'linear-gradient(var(--nm-welcome-grid) 1px, transparent 1px), linear-gradient(90deg, var(--nm-welcome-grid) 1px, transparent 1px)',
  backgroundSize: '72px 72px',
};

export interface WelcomeRailStep {
  id: WelcomeStepId;
  title: string;
  /** One line: what this step is, or — once done — what it produced. */
  detail: string;
}

export interface WelcomeRailProps {
  steps: WelcomeRailStep[];
  /** Index into `steps`; everything before it renders as done. */
  activeIndex: number;
  onLogout: () => void;
}

export function WelcomeRail({ steps, activeIndex, onLogout }: WelcomeRailProps) {
  const { t } = useTranslation();

  return (
    <>
      {/* md+ : the full rail */}
      <aside
        className="hidden w-[280px] shrink-0 flex-col border-r border-[var(--nm-hairline)] bg-[var(--nm-paper)] px-6 py-5 md:flex"
        style={WELCOME_GRID_STYLE}
      >
        {/* Bigger than the sidebar's lockup on purpose: on a first run this is
            the only place the product introduces itself (Owner 2026-08-27). */}
        <div className="flex items-center gap-2.5">
          <img src="/logo-light-mode.svg" alt="NarraNexus" className="h-11 w-auto dark:hidden" />
          <img
            src="/logo-dark-mode.svg"
            alt="NarraNexus"
            className="hidden h-11 w-auto dark:block"
          />
          <BetaBadge />
        </div>

        <ol className="my-auto flex flex-col">
          {steps.map((step, i) => {
            const state = i < activeIndex ? 'done' : i === activeIndex ? 'now' : 'upcoming';
            return (
              <li key={step.id} className="relative grid grid-cols-[18px_1fr] gap-2.5 pb-5 last:pb-0">
                {i < steps.length - 1 && (
                  <span className="absolute left-[8.5px] top-[19px] bottom-0.5 w-px bg-[var(--nm-hairline)]" />
                )}
                <span
                  className={cn(
                    'z-[1] grid h-[18px] w-[18px] place-items-center rounded-full border bg-[var(--nm-card)]',
                    state === 'done' && 'border-[var(--nm-ink)] bg-[var(--nm-ink)]',
                    state === 'now' && 'border-[1.5px] border-[var(--nm-ink)]',
                    state === 'upcoming' && 'border-[var(--nm-ink30)]',
                  )}
                >
                  {state === 'done' && <Check className="h-2.5 w-2.5 text-[var(--nm-paper)]" />}
                  {state === 'now' && (
                    <span className="h-[7px] w-[7px] rounded-full bg-[var(--nm-ink)]" />
                  )}
                </span>
                <div>
                  <div
                    className={cn(
                      'text-[13px] font-medium leading-snug tracking-tight',
                      state === 'upcoming' ? 'text-[var(--nm-ink50)]' : 'text-[var(--nm-ink)]',
                    )}
                  >
                    {step.title}
                  </div>
                  <div className="mt-0.5 text-[11px] leading-relaxed text-[var(--nm-ink50)]">
                    {step.detail}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>

        <div className="flex items-center gap-3 text-[11px] text-[var(--nm-ink50)]">
          <LanguageToggle />
          <button
            type="button"
            onClick={onLogout}
            className="inline-flex items-center gap-1.5 hover:text-[var(--nm-ink)]"
          >
            <LogOut className="h-3.5 w-3.5" />
            {t('pages.welcome.logout')}
          </button>
        </div>
      </aside>

      {/* < md : progress strip. Same data, one line. */}
      <div
        className="flex items-center gap-3 border-b border-[var(--nm-hairline)] bg-[var(--nm-paper)] px-4 py-3 md:hidden"
        style={WELCOME_GRID_STYLE}
      >
        <span className="flex flex-1 gap-1">
          {steps.map((step, i) => (
            <span
              key={step.id}
              className={cn(
                'h-0.5 flex-1 rounded-[var(--radius-xs)]',
                i <= activeIndex ? 'bg-[var(--nm-ink)]' : 'bg-[var(--nm-hairline)]',
              )}
            />
          ))}
        </span>
        <span className="shrink-0 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.10em] text-[var(--nm-ink50)]">
          {activeIndex + 1} / {steps.length}
        </span>
      </div>
    </>
  );
}
