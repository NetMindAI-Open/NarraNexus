/**
 * @file_name: JobRow.tsx
 * @author:
 * @date: 2026-08-27
 * @description: One job in the list view — two lines, no card chrome.
 *
 * Extracted from JobsPanel during the density rebuild. The old row was a 76px
 * bordered card carrying three separate expressions of the same fact (a 32px
 * status icon tile, a bordered status Badge, and a one-line-clamped
 * description that in practice truncated to a meaningless fragment such as
 * "Once a day, drop by with a fresh topic. Pause or…").
 *
 * Now: a 7px semantic dot, the title, the status word as plain mono, and a
 * second line carrying the thing users actually came to read — the schedule
 * and the next/last run. The description moved into JobExpandedDetail, where
 * it has room to be a whole sentence.
 *
 * Rows are separated by a hairline instead of each being its own bordered
 * card, which also retires the nested-radius problem (design_system §3.2):
 * there is no inner box left to give a radius to.
 */

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { describeRow, formatRelative, isAttentionStatus } from '@/lib/jobsPanelModel';
import { statusVisual, shouldTintStatusLabel } from './jobStatusVisuals';
import type { Job } from '@/types/api';

export interface JobRowProps {
  job: Job;
  expanded: boolean;
  onToggle: () => void;
  /** The expanded detail panel; rendered only while `expanded`. */
  children?: React.ReactNode;
}

export function JobRow({ job, expanded, onToggle, children }: JobRowProps) {
  const { t, i18n } = useTranslation();

  const visual = statusVisual(job.status);
  const attention = isAttentionStatus(job.status);
  const tinted = shouldTintStatusLabel(job.status);

  // Relative labels are computed at render, not on a timer: a running job's
  // "elapsed" advances on the next refresh. A per-row interval would re-render
  // the whole list every second for a cosmetic digit.
  const meta = useMemo(
    () => describeRow(job, { formatTime: (iso) => formatRelative(iso, i18n.language) }),
    [job, i18n.language],
  );

  const secondLine = [meta.schedule, meta.timing]
    .filter((s): s is NonNullable<typeof s> => s !== null)
    .map((s) => t(s.key, s.params))
    .join(' · ');

  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onToggle();
        }
      }}
      className={cn(
        'w-full text-left px-3.5 py-2.5 cursor-pointer group',
        'border-t border-[var(--rule)] first:border-t-0',
        'border-l-2 border-l-transparent transition-colors duration-150',
        'focus-visible:outline-none focus-visible:bg-[var(--nm-row-hover)]',
        'focus-visible:border-l-[var(--border-strong)]',
        attention && 'border-l-[var(--color-error)]',
        expanded
          ? 'bg-[var(--nm-row-active)] border-l-[var(--text-primary)]'
          : 'hover:bg-[var(--nm-row-hover)]',
        job.status === 'cancelled' && 'opacity-60',
      )}
    >
      <div className="flex items-start gap-2.5">
        {/* Semantic dot, not a filled icon — design_system §5 keeps the icon
            library linear-only and expresses "solid" state as geometry. */}
        <span
          aria-hidden="true"
          className={cn('w-[7px] h-[7px] rounded-full shrink-0 mt-[5px]', visual.hollow && 'border-[1.5px]')}
          style={
            visual.hollow
              ? { borderColor: visual.color }
              : { backgroundColor: visual.color }
          }
        />

        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2.5">
            <span
              className={cn(
                'flex-1 min-w-0 truncate text-[13px] font-semibold tracking-[-0.005em]',
                job.status === 'cancelled'
                  ? 'text-[var(--text-tertiary)] line-through'
                  : 'text-[var(--text-primary)]',
              )}
            >
              {job.title || t('jobs.untitled')}
            </span>
            <span
              className={cn(
                'shrink-0 text-[9px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em]',
                !tinted && 'text-[var(--text-tertiary)]',
              )}
              style={tinted ? { color: visual.color } : undefined}
            >
              {t(visual.labelKey)}
            </span>
          </div>

          {secondLine && (
            <div className="mt-0.5 truncate text-[10px] font-[family-name:var(--font-mono)] tabular-nums text-[var(--text-tertiary)]">
              {secondLine}
            </div>
          )}

          {expanded && children}
        </div>
      </div>
    </div>
  );
}

export default JobRow;
