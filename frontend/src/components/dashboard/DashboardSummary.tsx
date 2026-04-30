/**
 * @file_name: DashboardSummary.tsx
 * @description: v2.3 — top-of-page summary strip. Doubles as the color
 * legend so users learn what the rail colors mean by reading this once.
 *
 * Changes vs v2.1: total moved to a clear "N agents" anchor on the left;
 * each chip now shows its label inline (so users don't need to hover for
 * meaning); attention chips (error/warning/paused) come first to surface
 * what needs action. The "hover for meaning" hint is removed because every
 * label is already visible.
 */
import type { AgentStatus, AgentHealth } from '@/types';

interface ChipDef {
  health: AgentHealth;
  label: string;
  dotCls: string;
  textCls: string;
}

// Order: attention-first (error → warning → paused), then healthy states.
const CHIP_ORDER: ChipDef[] = [
  { health: 'error',           label: 'error',    dotCls: 'bg-[var(--color-red-500)]',     textCls: 'text-[var(--color-red-500)]' },
  { health: 'acknowledged',    label: 'ack',      dotCls: 'bg-slate-500',                  textCls: 'text-slate-600 dark:text-slate-400' },
  { health: 'warning',         label: 'blocked',  dotCls: 'bg-[var(--color-yellow-500)]',  textCls: 'text-[var(--color-yellow-500)]' },
  { health: 'paused',          label: 'paused',   dotCls: 'bg-[var(--color-yellow-500)]',  textCls: 'text-[var(--color-yellow-500)]' },
  { health: 'healthy_running', label: 'running',  dotCls: 'bg-[var(--color-green-500)]',   textCls: 'text-[var(--color-green-500)]' },
  { health: 'healthy_idle',    label: 'idle',     dotCls: 'bg-sky-500',                    textCls: 'text-sky-600 dark:text-sky-400' },
  { health: 'idle_long',       label: 'quiet',    dotCls: 'bg-gray-400',                   textCls: 'text-gray-500' },
];

const TOOLTIPS: Record<AgentHealth, string> = {
  healthy_running: 'Agent is actively working — no errors, no warnings',
  healthy_idle:    'Agent is idle but was active recently',
  idle_long:       'Agent has been idle for more than 72 hours',
  warning:         'Agent has at least one job blocked by dependencies',
  paused:          'Agent has at least one job paused by user',
  error:           'Agent has a failed job or recent error event',
  acknowledged:    'Error acknowledged by user — underlying issue still present',
};

export function DashboardSummary({ agents }: { agents: AgentStatus[] }) {
  const counts: Record<AgentHealth, number> = {
    healthy_running: 0,
    healthy_idle: 0,
    idle_long: 0,
    warning: 0,
    paused: 0,
    error: 0,
    acknowledged: 0,
  };
  for (const a of agents) {
    const h: AgentHealth = a.owned_by_viewer ? a.health : 'healthy_idle';
    counts[h] += 1;
  }
  const total = agents.length;
  if (total === 0) return null;

  const needsAttention = counts.error + counts.warning + counts.paused;

  return (
    <div
      data-testid="dashboard-summary"
      className="flex flex-wrap items-center gap-x-5 gap-y-1.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] px-4 py-2.5"
    >
      {/* Anchor — total agent count, the one number users always need */}
      <div className="flex items-baseline gap-1.5 tabular-nums">
        <span className="text-[18px] font-semibold text-[var(--text-primary)]">{total}</span>
        <span className="text-[10px] uppercase tracking-[0.1em] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)]">
          {total === 1 ? 'agent' : 'agents'}
        </span>
      </div>

      {/* Status header — only show when something is up */}
      {needsAttention > 0 && (
        <span className="text-[11px] uppercase tracking-[0.1em] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)]">
          needs attention
        </span>
      )}

      {/* Chips — only render states that have at least 1 agent */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs tabular-nums">
        {CHIP_ORDER.map((c) => {
          const n = counts[c.health];
          if (n === 0) return null;
          return (
            <span
              key={c.health}
              title={TOOLTIPS[c.health]}
              className="inline-flex items-baseline gap-1.5"
            >
              <span className={`inline-block h-2 w-2 rounded-full ${c.dotCls}`} aria-hidden />
              <span className="text-[var(--text-primary)] font-medium">{n}</span>
              <span className={`text-[11px] ${c.textCls}`}>{c.label}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}
