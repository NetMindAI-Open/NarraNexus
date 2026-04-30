/**
 * @file_name: QueueBar.tsx
 * @description: v2.3 — stacked bar + counts for all 6 live job states.
 * Compact mode (inline in collapsed card) shows bar + total + the
 * worrying states (failed/blocked) labeled in plain English. Full mode
 * shows all states with labels under the bar.
 */
import type { QueueCounts } from '@/types';

const SEGMENT_CLS: Record<keyof Omit<QueueCounts, 'total'>, string> = {
  running: 'bg-[var(--color-green-500)]',
  active: 'bg-sky-500',
  pending: 'bg-gray-400',
  blocked: 'bg-[var(--color-yellow-500)]',
  paused: 'bg-[var(--color-yellow-500)]',
  failed: 'bg-[var(--color-red-500)]',
};

const ORDER: Array<keyof Omit<QueueCounts, 'total'>> = [
  'running', 'active', 'pending', 'blocked', 'paused', 'failed',
];

const LABEL_SHORT: Record<keyof Omit<QueueCounts, 'total'>, string> = {
  running: 'running',
  active: 'active',
  pending: 'pending',
  blocked: 'blocked',
  paused: 'paused',
  failed: 'failed',
};

export function QueueBar({ queue, compact = false }: { queue: QueueCounts; compact?: boolean }) {
  if (!queue || queue.total === 0) {
    return null;
  }

  if (compact) {
    return (
      <div className="flex items-center gap-2 text-[11px] text-[var(--text-secondary)] tabular-nums">
        <div
          data-testid="queue-bar"
          className="flex h-1.5 w-20 overflow-hidden rounded-full bg-[var(--bg-tertiary)]"
          title={`${queue.total} jobs in queue`}
        >
          {ORDER.map((key) => {
            const count = queue[key];
            if (count === 0) return null;
            const pct = (count / queue.total) * 100;
            return (
              <div
                key={key}
                data-testid={`queue-seg-${key}`}
                className={SEGMENT_CLS[key]}
                style={{ width: `${pct}%` }}
                title={`${count} ${LABEL_SHORT[key]}`}
              />
            );
          })}
        </div>
        <span>
          <span className="text-[var(--text-primary)] font-medium">{queue.total}</span>
          <span className="ml-1 text-[10px] uppercase tracking-[0.08em] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)]">
            jobs
          </span>
        </span>
        {queue.failed > 0 && (
          <span
            className="text-[var(--color-red-500)] font-medium"
            title={`${queue.failed} failed`}
          >
            {queue.failed} failed
          </span>
        )}
        {queue.blocked > 0 && (
          <span
            className="text-[var(--color-yellow-500)] font-medium"
            title={`${queue.blocked} blocked`}
          >
            {queue.blocked} blocked
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-[0.08em] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)]">
          Queue
        </span>
        <div
          data-testid="queue-bar"
          className="flex h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--bg-tertiary)]"
        >
          {ORDER.map((key) => {
            const count = queue[key];
            if (count === 0) return null;
            const pct = (count / queue.total) * 100;
            return (
              <div
                key={key}
                data-testid={`queue-seg-${key}`}
                className={SEGMENT_CLS[key]}
                style={{ width: `${pct}%` }}
                title={`${count} ${LABEL_SHORT[key]}`}
              />
            );
          })}
        </div>
        <span className="text-xs font-medium text-[var(--text-primary)] tabular-nums">{queue.total}</span>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-[var(--text-secondary)] tabular-nums">
        {ORDER.map((key) => {
          const count = queue[key];
          if (count === 0) return null;
          return (
            <span key={key} className="inline-flex items-center gap-1">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${SEGMENT_CLS[key]}`} />
              <span className="text-[var(--text-primary)] font-medium">{count}</span>
              <span className="text-[var(--text-tertiary)]">{LABEL_SHORT[key]}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}
