/**
 * @file_name: jobStatusVisuals.ts
 * @author:
 * @date: 2026-08-27
 * @description: One status → color/label map, shared by the meter and the rows.
 *
 * Before the density rebuild the mapping lived inline in JobsPanel as
 * `statusConfig` (icon + text class + bg class) while StatusDistributionBar
 * hard-coded its own segment colors — two lists that had to agree and had no
 * mechanism forcing them to. There is now one table.
 *
 * Colors are semantic tokens only (design_system §2): no palette primitives,
 * no hex. Status is expressed as a colored geometric dot rather than a filled
 * icon, per §5 — the icon library stays linear-only.
 */

import type { JobStatus } from '@/types/api';

/** Neutral: the job has a state, but not one that wants the eye. */
const NEUTRAL = 'var(--text-tertiary)';

export interface StatusVisual {
  /** Dot fill and meter-segment color. */
  color: string;
  /** Render an outline ring instead of a filled dot — nothing has run yet. */
  hollow: boolean;
  /** i18n key for the status word. */
  labelKey: string;
}

const VISUALS: Record<JobStatus, StatusVisual> = {
  failed:          { color: 'var(--color-error)',   hollow: false, labelKey: 'jobs.status.failed' },
  blocked_failed:  { color: 'var(--color-error)',   hollow: false, labelKey: 'jobs.status.blockedFailed' },
  paused_no_quota: { color: 'var(--color-warning)', hollow: false, labelKey: 'jobs.status.pausedNoQuota' },
  paused_spend_cap: { color: 'var(--color-warning)', hollow: false, labelKey: 'jobs.status.pausedSpendCap' },
  running:         { color: 'var(--color-warning)', hollow: false, labelKey: 'jobs.status.running' },
  // "Active" = the Module instance is alive but no script is executing. Ink
  // rather than a semantic color: it is normal, not noteworthy.
  active:          { color: 'var(--text-primary)',  hollow: false, labelKey: 'jobs.status.active' },
  cooling:         { color: 'var(--color-warning)', hollow: false, labelKey: 'jobs.status.cooling' },
  pending:         { color: NEUTRAL,                hollow: true,  labelKey: 'jobs.status.pending' },
  blocked:         { color: NEUTRAL,                hollow: true,  labelKey: 'jobs.status.blocked' },
  paused:          { color: NEUTRAL,                hollow: false, labelKey: 'jobs.status.paused' },
  completed:       { color: 'var(--color-success)', hollow: false, labelKey: 'jobs.status.completed' },
  cancelled:       { color: NEUTRAL,                hollow: false, labelKey: 'jobs.status.cancelled' },
};

export function statusVisual(status: JobStatus): StatusVisual {
  return VISUALS[status] ?? VISUALS.pending;
}

/**
 * Whether the status word itself should carry its semantic color.
 *
 * Only statuses that are actually noteworthy get tinted; everything else keeps
 * the row monochrome so the few colored words in a long list mean something.
 */
export function shouldTintStatusLabel(status: JobStatus): boolean {
  return statusVisual(status).color !== NEUTRAL && status !== 'active';
}
