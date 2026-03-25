/**
 * EmbeddingStatus - Vector index status panel
 *
 * Shows embedding rebuild progress per entity type.
 * Displayed in the settings/config area. Auto-polls when rebuild is active.
 */

import { useEffect } from 'react';
import { cn } from '@/lib/utils';
import { useEmbeddingStore } from '@/stores/embeddingStore';

const ENTITY_LABELS: Record<string, string> = {
  narrative: 'Narrative',
  event: 'Event',
  job: 'Job',
  entity: 'Entity',
};

function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500',
            pct >= 100
              ? 'bg-[var(--color-success)]'
              : 'bg-[var(--accent-primary)]'
          )}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <span className="text-[10px] font-mono text-[var(--text-tertiary)] w-16 text-right">
        {value}/{max}
        {pct >= 100 && ' \u2713'}
      </span>
    </div>
  );
}

export function EmbeddingStatus() {
  const { status, loading, fetchStatus, startRebuild, startPolling } = useEmbeddingStore();

  // Fetch on mount and start polling if rebuild is active
  useEffect(() => {
    fetchStatus();
    startPolling();
    return () => useEmbeddingStore.getState().stopPolling();
  }, []);

  if (!status) {
    if (loading) {
      return (
        <div className="p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)]">
          <div className="text-xs text-[var(--text-tertiary)] animate-pulse">
            Loading embedding status...
          </div>
        </div>
      );
    }
    return null;
  }

  const { all_done, model, stats, migration } = status;
  const isRebuilding = migration.is_running;

  // All done: compact success state
  if (all_done && !isRebuilding) {
    return (
      <div className="p-3 rounded-xl border border-[var(--color-success)]/20 bg-[var(--color-success)]/5">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded-full bg-[var(--color-success)]/20 flex items-center justify-center">
            <span className="text-[var(--color-success)] text-xs">{'\u2713'}</span>
          </div>
          <span className="text-xs text-[var(--text-secondary)]">
            Vector index ready
          </span>
          <span className="text-[10px] font-mono text-[var(--text-tertiary)] ml-auto">
            {model}
          </span>
        </div>
      </div>
    );
  }

  // Compute total missing
  const totalMissing = Object.values(stats).reduce((sum, s) => sum + s.missing, 0);
  const totalEntities = Object.values(stats).reduce((sum, s) => sum + s.total, 0);

  return (
    <div className="p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {isRebuilding ? (
            <div className="w-2 h-2 rounded-full bg-[var(--accent-primary)] animate-pulse" />
          ) : (
            <div className="w-2 h-2 rounded-full bg-[var(--color-warning)]" />
          )}
          <span className="text-xs font-medium text-[var(--text-primary)]">
            {isRebuilding ? 'Rebuilding vector index...' : 'Vector index incomplete'}
          </span>
        </div>
        <span className="text-[10px] font-mono text-[var(--text-tertiary)]">
          {model}
        </span>
      </div>

      {/* Per-entity progress */}
      <div className="space-y-2">
        {Object.entries(stats).map(([type, s]) => (
          <div key={type} className="space-y-0.5">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-[var(--text-tertiary)] uppercase tracking-wider">
                {ENTITY_LABELS[type] || type}
              </span>
            </div>
            <ProgressBar value={s.migrated} max={s.total} />
          </div>
        ))}
      </div>

      {/* Overall progress when rebuilding */}
      {isRebuilding && (
        <div className="pt-2 border-t border-[var(--border-subtle)]">
          <div className="flex items-center justify-between text-[10px] text-[var(--text-tertiary)]">
            <span>Progress: {migration.progress_pct}%</span>
            <span>
              {migration.completed_count}/{migration.total_count}
            </span>
          </div>
        </div>
      )}

      {/* Error display */}
      {migration.error && (
        <div className="text-[10px] text-[var(--color-error)] bg-[var(--color-error)]/10 p-2 rounded">
          {migration.error}
        </div>
      )}

      {/* Rebuild button (only when not rebuilding and not all done) */}
      {!isRebuilding && totalMissing > 0 && (
        <button
          onClick={async () => {
            const ok = await startRebuild();
            if (ok) startPolling();
          }}
          className={cn(
            'w-full py-1.5 px-3 rounded-lg text-xs font-medium transition-all',
            'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]',
            'hover:bg-[var(--accent-primary)]/20',
            'border border-[var(--accent-primary)]/20'
          )}
        >
          Rebuild ({totalMissing} missing of {totalEntities})
        </button>
      )}
    </div>
  );
}
