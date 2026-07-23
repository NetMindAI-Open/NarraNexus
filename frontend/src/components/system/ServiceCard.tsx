/**
 * @file_name: ServiceCard.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Service status card for the System page
 *
 * Displays a single service's status with indicator dot, port info,
 * and optional restart button.
 *
 * 2026-07-22: optional expandable per-worker detail. The consolidated
 * `workers` supervisor runs four sub-workers (poller / jobs / bus / channels)
 * in one process, so the process-level dot can read "running" while a
 * sub-worker crash-loops. When `workers` is passed, the card shows a flap
 * warning (any sub-worker restarting / restartCount>0) and an expandable list
 * of each sub-worker's state + cumulative restart count.
 */

import { useState } from 'react';
import { RotateCw, ChevronRight, ChevronDown, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent, Button } from '@/components/ui';
import { cn } from '@/lib/utils';
import type { WorkerLiveness, WorkerState } from '@/types/platform';

type ServiceStatus =
  | 'stopped'
  | 'starting'
  | 'running'
  | 'crashed'
  | 'healthy'
  | 'unhealthy'
  | 'unknown';

interface ServiceCardProps {
  label: string;
  status: ServiceStatus;
  port: number | null;
  lastError: string | null;
  onRestart?: () => void;
  /** Per-worker liveness for the consolidated `workers` service (optional). */
  workers?: WorkerLiveness[];
  /** Age of the worker liveness snapshot (seconds); null when unknown. Older
   *  than STALE_AFTER_S means the supervisor may be dead and the snapshot is a
   *  frozen last-known state, so we must not render it as live. */
  workerHeartbeatAgeSeconds?: number | null;
}

// 3× the supervisor's 30s heartbeat cadence. Beyond this the snapshot is stale.
const STALE_AFTER_S = 90;

function formatAge(seconds: number): string {
  return seconds >= 60 ? `${Math.floor(seconds / 60)}m` : `${Math.round(seconds)}s`;
}

const STATUS_CONFIG: Record<
  ServiceStatus,
  { color: string; bg: string; pulse: boolean }
> = {
  healthy: {
    color: 'bg-[var(--color-success)]',
    bg: 'shadow-[0_0_8px_var(--color-success)]',
    pulse: true,
  },
  running: {
    color: 'bg-[var(--color-success)]',
    bg: 'shadow-[0_0_8px_var(--color-success)]',
    pulse: true,
  },
  starting: {
    color: 'bg-[var(--color-warning)]',
    bg: 'shadow-[0_0_8px_var(--color-warning)]',
    pulse: true,
  },
  crashed: {
    color: 'bg-[var(--color-error)]',
    bg: 'shadow-[0_0_8px_var(--color-error)]',
    pulse: false,
  },
  unhealthy: {
    color: 'bg-[var(--color-error)]',
    bg: 'shadow-[0_0_8px_var(--color-error)]',
    pulse: false,
  },
  stopped: {
    color: 'bg-[var(--text-tertiary)]',
    bg: '',
    pulse: false,
  },
  unknown: {
    color: 'bg-[var(--text-tertiary)]',
    bg: '',
    pulse: false,
  },
};

/** Dot color per sub-worker state (subset of the service palette). */
const WORKER_DOT: Record<WorkerState, string> = {
  running: 'bg-[var(--color-success)]',
  starting: 'bg-[var(--color-warning)]',
  restarting: 'bg-[var(--color-error)]',
  stopped: 'bg-[var(--text-tertiary)]',
  unknown: 'bg-[var(--text-tertiary)]',
};

export function ServiceCard({
  label,
  status,
  port,
  lastError,
  onRestart,
  workers,
  workerHeartbeatAgeSeconds,
}: ServiceCardProps) {
  const { t } = useTranslation();
  const config = STATUS_CONFIG[status];
  const [expanded, setExpanded] = useState(false);

  const hasWorkers = !!workers && workers.length > 0;
  // Stale = the liveness snapshot hasn't refreshed within 3 heartbeats. The
  // service_audit heartbeat row is persistent, so a dead supervisor still
  // yields available:true with a frozen snapshot — rendering it as live would
  // just swap this card's original failure mode ("process green hides a dead
  // worker") for a new one ("stale snapshot hides a dead supervisor").
  const stale =
    hasWorkers &&
    workerHeartbeatAgeSeconds != null &&
    workerHeartbeatAgeSeconds > STALE_AFTER_S;
  // Flap warning only reflects a worker CURRENTLY restarting (not a long-ago
  // restart that left restartCount>0 — that stays as the per-row count badge).
  // Suppressed when stale, since we can't trust the snapshot.
  const flapping =
    hasWorkers && !stale && workers!.some((w) => w.state === 'restarting');

  return (
    <Card variant="default">
      <CardContent className="flex flex-col gap-2">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            {/* Header row: indicator + label */}
            <div className="flex items-center gap-2.5 mb-2">
              <span className="relative flex h-3 w-3">
                {config.pulse && (
                  <span
                    className={cn(
                      'absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping',
                      config.color,
                    )}
                  />
                )}
                <span
                  className={cn(
                    'relative inline-flex rounded-full h-3 w-3',
                    config.color,
                    config.bg,
                  )}
                />
              </span>
              <span className="text-sm font-semibold text-[var(--text-primary)] truncate">
                {label}
              </span>
              {flapping && (
                <span
                  className="flex items-center gap-1 text-[var(--color-warning)]"
                  title={t('system.serviceCard.workersFlapping')}
                >
                  <AlertTriangle className="w-3.5 h-3.5" />
                </span>
              )}
              {stale && (
                <span
                  className="text-xs text-[var(--text-tertiary)]"
                  title={t('system.serviceCard.workersStaleHint')}
                >
                  {t('system.serviceCard.workersStale', {
                    age: formatAge(workerHeartbeatAgeSeconds!),
                  })}
                </span>
              )}
            </div>

            {/* Status + port */}
            <div className="flex items-center gap-3 text-xs text-[var(--text-secondary)]">
              <span>{t(`system.serviceStatus.${status}`)}</span>
              {port != null && (
                <span className="font-mono text-[var(--text-tertiary)]">
                  :{port}
                </span>
              )}
            </div>

            {/* Error message */}
            {lastError && (
              <p className="mt-2 text-xs text-[var(--color-error)] truncate">
                {lastError}
              </p>
            )}
          </div>

          {/* Restart button */}
          {onRestart && (
            <Button
              variant="ghost"
              size="icon"
              onClick={onRestart}
              title={t('system.serviceCard.restart')}
              className="shrink-0"
            >
              <RotateCw className="w-4 h-4" />
            </Button>
          )}
        </div>

        {/* Expandable per-worker detail (only for the consolidated `workers`) */}
        {hasWorkers && (
          <div className="border-t border-[var(--border-subtle)] pt-1.5">
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center gap-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
            >
              {expanded ? (
                <ChevronDown className="w-3.5 h-3.5" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5" />
              )}
              <span>
                {t('system.serviceCard.workerCount', { count: workers!.length })}
              </span>
            </button>

            {expanded && (
              <ul className="mt-1.5 flex flex-col gap-1">
                {workers!.map((w) => (
                  <li
                    key={w.name}
                    className="flex items-center gap-2 text-xs"
                    title={w.lastError ?? undefined}
                  >
                    <span
                      className={cn(
                        'inline-flex rounded-full h-2 w-2 shrink-0',
                        // Stale snapshot → gray every dot; we can't vouch for
                        // the frozen state.
                        stale
                          ? WORKER_DOT.unknown
                          : WORKER_DOT[w.state] ?? WORKER_DOT.unknown,
                      )}
                    />
                    <span className="text-[var(--text-primary)] font-mono">
                      {w.name}
                    </span>
                    <span className="text-[var(--text-tertiary)]">
                      {t(`system.workerState.${w.state}`)}
                    </span>
                    {w.restartCount > 0 && (
                      <span className="text-[var(--color-warning)]">
                        {t('system.serviceCard.restarts', {
                          count: w.restartCount,
                        })}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export type { ServiceCardProps, ServiceStatus };
