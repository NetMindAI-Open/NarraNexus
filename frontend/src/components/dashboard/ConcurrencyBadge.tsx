/**
 * @file_name: ConcurrencyBadge.tsx
 * @description: Render ×bucket for public agents (where verb_line is unavailable
 * due to permission boundary).
 *
 * v2.1.1 change: REMOVED for owned agents. The kind+running_count combo
 * ("Job ×4" / "Callback ×2") was misleading because running_count summed
 * sessions + jobs + instances of different kinds. For owned agents the
 * server-derived `verb_line` ("Running 4 jobs" / "Serving 3 users") now
 * conveys both type AND count in one human phrase.
 */
import type { AgentStatus } from '@/types';

export function ConcurrencyBadge({ agent }: { agent: AgentStatus }) {
  // Owned agents: verb_line covers count semantics. No badge.
  if (agent.owned_by_viewer) return null;
  // Public non-owned: bucketed concurrency only.
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
