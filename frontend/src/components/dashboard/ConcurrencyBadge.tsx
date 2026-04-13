/**
 * @file_name: ConcurrencyBadge.tsx
 * @description: Render ×N (owned) or ×bucket (public). Hidden when count/bucket indicates no concurrency.
 */
import type { AgentStatus } from '@/types';

export function ConcurrencyBadge({ agent }: { agent: AgentStatus }) {
  if (agent.owned_by_viewer) {
    if (agent.running_count <= 1) return null;
    return (
      <span
        data-testid="concurrency-badge"
        className="text-xs font-mono text-[var(--text-secondary)]"
      >
        ×{agent.running_count}
      </span>
    );
  }
  if (agent.running_count_bucket === '0') return null;
  return (
    <span
      data-testid="concurrency-badge"
      className="text-xs font-mono text-[var(--text-secondary)]"
    >
      ×{agent.running_count_bucket}
    </span>
  );
}
