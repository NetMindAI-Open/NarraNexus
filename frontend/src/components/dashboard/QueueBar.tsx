/**
 * @file_name: QueueBar.tsx
 * @description: v2.1.1 — stacked bar + counts for all 6 live job states.
 * Compact mode (inline in collapsed card) shows just the bar + total + the
 * top 2 worrying states (failed/blocked first). Full mode shows all states
 * with labels.
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
  running: 'running',
  active: 'active',
  pending: 'pending',
  blocked: 'blocked',
  paused: 'paused',
  failed: 'failed',
};

export function QueueBar({ queue, compact = false }: { queue: QueueCounts; compact?: boolean }) {
  if (!queue || queue.total === 0) {
    // Don't render at all when empty — was visual noise before.
    return null;
  }

  if (compact) {
    // Inline strip: tiny bar + total + only states that need attention
    return (
      <div className="flex items-center gap-1.5 text-[11px] font-mono text-[var(--text-secondary)]">
        <span>Q</span>
        <div
          data-testid="queue-bar"
          className="flex h-1.5 w-16 overflow-hidden rounded-full bg-[var(--bg-tertiary)]"
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
        <span>{queue.total}</span>
        {queue.failed > 0 && (
          <span className="text-red-500" title={`${queue.failed} failed`}>· 🔴 {queue.failed}</span>
        )}
        {queue.blocked > 0 && (
          <span className="text-amber-600" title={`${queue.blocked} blocked`}>· 🟠 {queue.blocked}</span>
        )}
      </div>
    );
  }

  // Full (in expanded section)
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
                title={`${count} ${LABEL_SHORT[key]}`}
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
