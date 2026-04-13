/**
 * @file_name: AgentCard.tsx
 * @description: 3+1 main-view card. Subject to G005: contains exactly
 * status badge + action line + duration + (conditional) concurrency badge.
 * Click toggles expand. React default-escapes action_line — DO NOT switch to
 * dangerouslySetInnerHTML (banned via eslint no-restricted-syntax).
 */
import type { AgentStatus } from '@/types';
import { StatusBadge } from './StatusBadge';
import { DurationDisplay } from './DurationDisplay';
import { ConcurrencyBadge } from './ConcurrencyBadge';

const EM_DASH = '\u2014';

interface Props {
  agent: AgentStatus;
  onToggleExpand: () => void;
  expanded?: boolean;
}

export function AgentCard({ agent, onToggleExpand, expanded }: Props) {
  const actionLine = agent.owned_by_viewer ? agent.action_line : null;
  const displayLine = actionLine ?? EM_DASH;
  return (
    <div
      data-testid={`agent-card-${agent.agent_id}`}
      data-expanded={expanded ? 'true' : 'false'}
      onClick={onToggleExpand}
      className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-glass)] p-4 cursor-pointer hover:border-[var(--accent-primary)] transition-colors"
    >
      <div className="flex items-center justify-between">
        <div className="font-semibold text-sm">{agent.name}</div>
        <ConcurrencyBadge agent={agent} />
      </div>
      <div className="mt-2 flex items-center gap-3 text-sm">
        <StatusBadge kind={agent.status.kind} />
        <span
          data-testid="action-line"
          className="flex-1 truncate text-[var(--text-secondary)]"
          title={displayLine}
        >
          {displayLine}
        </span>
        <DurationDisplay startedAt={agent.status.started_at} />
      </div>
    </div>
  );
}
