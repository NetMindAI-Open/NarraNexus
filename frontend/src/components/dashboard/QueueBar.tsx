/**
 * @file_name: QueueBar.tsx
 * @description: v2.1 — stacked bar + counts for all 6 live job states.
 * Each segment width is proportional to count. Zero-width segments render
 * nothing to avoid visual clutter.
 */
import type { QueueCounts } from '@/types';

const SEGMENT_CLS: Record<keyof Omit<QueueCounts, 'total'>, string> = {
  running: 'bg-emerald-500',
  active: 'bg-sky-500',
  pending: 'bg-gray-400',
  blocked: 'bg-amber-500',
  paused: 'bg-yellow-500',
  failed: 'bg-red-500',
};

const ORDER: Array<keyof Omit<QueueCounts, 'total'>> = [
  'running', 'active', 'pending', 'blocked', 'paused', 'failed',
];

const LABEL_SHORT: Record<keyof Omit<QueueCounts, 'total'>, string> = {
  running: 'R',
  active: 'A',
  pending: 'P',
  blocked: 'B',
  paused: 'Pa',
  failed: 'F',
};

export function QueueBar({ queue }: { queue: QueueCounts }) {
  if (!queue || queue.total === 0) {
    return (
      <div data-testid="queue-bar-empty" className="text-xs text-[var(--text-secondary)]">
        Queue · empty
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <span className="text-xs text-[var(--text-secondary)]">Queue</span>
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
                title={`${count} ${key}`}
              />
            );
          })}
        </div>
        <span className="text-xs font-mono text-[var(--text-secondary)]">{queue.total}</span>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] font-mono text-[var(--text-secondary)]">
        {ORDER.map((key) => {
          const count = queue[key];
          if (count === 0) return null;
          return (
            <span key={key} className="flex items-center gap-1">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${SEGMENT_CLS[key]}`} />
              {count} {LABEL_SHORT[key]}
            </span>
          );
        })}
      </div>
    </div>
  );
}
