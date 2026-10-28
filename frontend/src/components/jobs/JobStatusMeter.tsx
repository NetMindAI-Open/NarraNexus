/**
 * @file_name: JobStatusMeter.tsx
 * @author:
 * @date: 2026-08-27
 * @description: One-line health readout for the Jobs panel — bar + inline legend.
 *
 * Replaces the pair of bands that used to sit at the top of the panel: a
 * four-tile StatStrip (Active / Success / Failed / Rate) and a separate
 * StatusDistributionBar with its own title row. They described the same data
 * twice in ~176px. This is the same information in ~34px, and it only renders
 * when there is a distribution worth drawing (see `shouldShowMeter`).
 *
 * The legend is flow-laid rather than a fixed four-column grid, which is what
 * produced the truncated "SUCCES…" label in a 400px drawer.
 */

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { meterSegments, shouldShowMeter, successRate } from '@/lib/jobsPanelModel';
import { statusVisual } from './jobStatusVisuals';
import type { Job } from '@/types/api';

export function JobStatusMeter({ jobs }: { jobs: Job[] }) {
  const { t } = useTranslation();

  const model = useMemo(
    () => ({
      visible: shouldShowMeter(jobs),
      segments: meterSegments(jobs),
      rate: successRate(jobs),
    }),
    [jobs],
  );

  if (!model.visible) return null;

  return (
    <div
      data-nm="job-status-meter"
      className="px-3.5 py-2.5 flex flex-col gap-2 border-b border-[var(--rule)]"
    >
      {/* Proportional bar. 3px: a rule with weight, not a chart. */}
      <div className="h-[3px] rounded-full overflow-hidden flex gap-px">
        {model.segments.map((seg) => (
          <div
            key={seg.status}
            className="transition-[flex-grow] duration-500"
            style={{ flexGrow: seg.ratio, backgroundColor: statusVisual(seg.status).color }}
            title={`${t(statusVisual(seg.status).labelKey)}: ${seg.count}`}
          />
        ))}
      </div>

      <div className="flex items-center gap-3 min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
          {model.segments.map((seg) => (
            <span
              key={seg.status}
              className="flex items-center gap-1.5 text-[9px] font-[family-name:var(--font-mono)] uppercase tracking-[0.08em] text-[var(--text-tertiary)] whitespace-nowrap"
            >
              <i
                className="w-[5px] h-[5px] rounded-full shrink-0"
                style={{ backgroundColor: statusVisual(seg.status).color }}
              />
              <span className="text-[var(--text-secondary)] tabular-nums">{seg.count}</span>
              {t(statusVisual(seg.status).labelKey)}
            </span>
          ))}
        </div>

        {/* Null when nothing has finished — a flat "0%" on a fresh agent reads
            as "everything failed" rather than "no data yet". */}
        {model.rate !== null && (
          <span
            className="ml-auto shrink-0 text-[9px] font-[family-name:var(--font-mono)] tabular-nums text-[var(--text-secondary)]"
            title={t('jobs.metrics.rate')}
          >
            {model.rate}%
          </span>
        )}
      </div>
    </div>
  );
}

export default JobStatusMeter;
