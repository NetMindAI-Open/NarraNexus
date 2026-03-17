/**
 * StatusDistributionBar - Visual status distribution chart for jobs
 * Shows a stacked bar with color-coded segments per status
 */

import { useMemo } from 'react';
import type { Job } from '@/types/api';

export function StatusDistributionBar({ jobs }: { jobs: Job[] }) {
  const stats = useMemo(() => {
    const counts = {
      completed: jobs.filter((j) => j.status === 'completed').length,
      running: jobs.filter((j) => j.status === 'running').length,
      active: jobs.filter((j) => j.status === 'active').length,
      paused: jobs.filter((j) => j.status === 'paused').length,
      pending: jobs.filter((j) => j.status === 'pending').length,
      failed: jobs.filter((j) => j.status === 'failed').length,
      cancelled: jobs.filter((j) => j.status === 'cancelled').length,
    };
    return counts;
  }, [jobs]);

  const total = jobs.length || 1;

  if (jobs.length === 0) return null;

  return (
    <div className="space-y-2">
      <div className="flex justify-between text-[9px] font-mono text-[var(--text-tertiary)]">
        <span>Status Distribution</span>
        <span>{jobs.length} total</span>
      </div>
      <div className="h-2 bg-[var(--bg-tertiary)] rounded-full overflow-hidden flex">
        {stats.completed > 0 && (
          <div
            className="bg-[var(--color-success)] transition-all duration-500"
            style={{ width: `${(stats.completed / total) * 100}%` }}
            title={`Completed: ${stats.completed}`}
          />
        )}
        {stats.running > 0 && (
          <div
            className="bg-[var(--color-warning)] transition-all duration-500"
            style={{ width: `${(stats.running / total) * 100}%` }}
            title={`Running: ${stats.running}`}
          />
        )}
        {stats.active > 0 && (
          <div
            className="bg-[var(--accent-primary)] transition-all duration-500"
            style={{ width: `${(stats.active / total) * 100}%` }}
            title={`Active: ${stats.active}`}
          />
        )}
        {stats.paused > 0 && (
          <div
            className="bg-[var(--accent-secondary)] transition-all duration-500"
            style={{ width: `${(stats.paused / total) * 100}%` }}
            title={`Paused: ${stats.paused}`}
          />
        )}
        {stats.pending > 0 && (
          <div
            className="bg-[var(--text-tertiary)] transition-all duration-500"
            style={{ width: `${(stats.pending / total) * 100}%` }}
            title={`Pending: ${stats.pending}`}
          />
        )}
        {stats.failed > 0 && (
          <div
            className="bg-[var(--color-error)] transition-all duration-500"
            style={{ width: `${(stats.failed / total) * 100}%` }}
            title={`Failed: ${stats.failed}`}
          />
        )}
        {stats.cancelled > 0 && (
          <div
            className="bg-[var(--bg-tertiary)] transition-all duration-500"
            style={{ width: `${(stats.cancelled / total) * 100}%` }}
            title={`Cancelled: ${stats.cancelled}`}
          />
        )}
      </div>
      <div className="flex flex-wrap gap-2 text-[8px] font-mono">
        {stats.completed > 0 && (
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-[var(--color-success)]" />
            <span className="text-[var(--text-tertiary)]">{stats.completed} completed</span>
          </span>
        )}
        {stats.running > 0 && (
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-[var(--color-warning)]" />
            <span className="text-[var(--text-tertiary)]">{stats.running} running</span>
          </span>
        )}
        {stats.active > 0 && (
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-[var(--accent-primary)]" />
            <span className="text-[var(--text-tertiary)]">{stats.active} active</span>
          </span>
        )}
        {stats.paused > 0 && (
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-[var(--accent-secondary)]" />
            <span className="text-[var(--text-tertiary)]">{stats.paused} paused</span>
          </span>
        )}
        {stats.failed > 0 && (
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-[var(--color-error)]" />
            <span className="text-[var(--text-tertiary)]">{stats.failed} failed</span>
          </span>
        )}
      </div>
    </div>
  );
}
