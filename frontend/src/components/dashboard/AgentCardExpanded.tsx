/**
 * @file_name: AgentCardExpanded.tsx
 * @description: Owner-only expanded view.
 * Shows active sessions + pending jobs + enhanced signals.
 * For public non-owned agents, shows a placeholder.
 */
import type { AgentStatus } from '@/types';

export function AgentCardExpanded({ agent }: { agent: AgentStatus }) {
  if (!agent.owned_by_viewer) {
    return (
      <div
        data-testid={`agent-card-expanded-${agent.agent_id}`}
        className="mt-3 p-3 text-sm text-[var(--text-secondary)] italic"
      >
        Details available only to owner.
      </div>
    );
  }
  const e = agent.enhanced;
  return (
    <div
      data-testid={`agent-card-expanded-${agent.agent_id}`}
      className="mt-3 p-3 space-y-3 text-sm bg-[var(--bg-secondary)] rounded-lg"
    >
      {agent.sessions.length > 0 && (
        <div>
          <div className="font-medium mb-1">Active sessions</div>
          <ul className="space-y-1">
            {agent.sessions.map((s) => (
              <li key={s.session_id} className="flex gap-2 text-xs">
                <span>{s.user_display}</span>
                <span className="text-[var(--text-secondary)]">· {s.channel}</span>
                <span className="text-[var(--text-secondary)]">· {s.started_at}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {agent.pending_jobs.length > 0 && (
        <div>
          <div className="font-medium mb-1">Pending jobs</div>
          <ul className="space-y-1">
            {agent.pending_jobs.map((j) => (
              <li key={j.job_id} className="flex gap-2 text-xs">
                <span>{j.title}</span>
                <span className="text-[var(--text-secondary)]">· {j.job_type}</span>
                <span className="text-[var(--text-secondary)]">· {j.next_run_time ?? '\u2014'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <Stat label="Errors (1h)" value={String(e.recent_errors_1h)} />
        <Stat
          label="Token rate (1h)"
          value={e.token_rate_1h !== null ? String(e.token_rate_1h) : 'N/A'}
        />
        <Stat label="Active narratives" value={String(e.active_narratives)} />
        <Stat label="Unread bus msgs" value={String(e.unread_bus_messages)} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between px-2 py-1 rounded bg-[var(--bg-tertiary)]">
      <span className="text-[var(--text-secondary)]">{label}</span>
      <span className="font-mono">{value}</span>
    </div>
  );
}
