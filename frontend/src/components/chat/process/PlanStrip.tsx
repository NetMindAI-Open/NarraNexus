/**
 * @file_name: PlanStrip.tsx
 * @date: 2026-08-31
 * @description: The agent's live plan, pinned above the composer.
 *
 * Why it stayed pinned while everything else moved into the document: the
 * plan answers "where are we now". Folded into the flow it would scroll away
 * exactly when a long turn makes it most useful. That was already the reason
 * the retired ProcessPanel pinned it below its scroll area.
 *
 * Why it is no longer in a box: pinned is a position, not a register. The
 * strip sits on the page ground with one hairline top rule as the separator
 * (design_system §2.6) — no fill, no radius, no shadow, nothing that would
 * read as a second document beside the agent's turn.
 *
 * Plans are full snapshots (replace-on-write), so only the latest event
 * renders; and no plan means no strip at all, since a permanent empty rule
 * above the composer would be an unexplained line.
 */
import { memo, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { TurnEvent } from '@/types';
import { cn } from '@/lib/utils';

export interface PlanStripProps {
  events: TurnEvent[];
}

export const PlanStrip = memo(function PlanStrip({ events }: PlanStripProps) {
  const { t } = useTranslation();

  // Replace-on-write: each update carries the whole plan, so take the last.
  const plan = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const e = events[i];
      if (e.type === 'plan') return e;
    }
    return undefined;
  }, [events]);

  if (!plan) return null;

  const done = plan.steps.filter((s) => s.status === 'completed').length;

  return (
    <div
      data-testid="process-plan"
      className="mb-2 border-t border-[var(--border-subtle)] px-1 pt-2 text-xs"
      style={{ fontFamily: 'var(--font-mono)' }}
    >
      <div className="mb-1.5 flex items-center gap-2">
        <span
          className="text-[10px] uppercase tracking-[0.18em]"
          style={{ color: 'var(--nm-ink50)' }}
        >
          {t('chat.process.plan', 'Plan')}
        </span>
        <span className="text-[10px] tabular-nums" style={{ color: 'var(--nm-ink50)' }}>
          {done}/{plan.steps.length}
        </span>
        {/* Mini progress bar: width follows completed/total. */}
        <span
          className="ml-1 h-1 flex-1 overflow-hidden rounded-full"
          style={{ background: 'var(--nm-ink30)', maxWidth: 96 }}
        >
          <span
            className="block h-full rounded-full transition-[width] duration-500"
            style={{
              width: `${plan.steps.length ? Math.round((done / plan.steps.length) * 100) : 0}%`,
              background: 'var(--color-success)',
            }}
          />
        </span>
      </div>
      <div className="space-y-0.5">
        {plan.steps.map((s, i) => {
          const active = s.status === 'in_progress';
          const settled = s.status === 'completed';
          return (
            <div
              key={`${i}-${s.step}`}
              className="flex items-center gap-2"
              style={{
                color: active
                  ? 'var(--color-silicon)'
                  : settled
                    ? 'var(--nm-ink50)'
                    : 'var(--nm-ink70)',
                fontWeight: active ? 600 : 400,
              }}
            >
              <span aria-hidden="true" className="shrink-0 select-none">
                {settled ? '✓' : active ? '▶' : '○'}
              </span>
              <span className={cn(settled && 'line-through')}>{s.step}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
});
