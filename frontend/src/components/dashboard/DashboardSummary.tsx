/**
 * @file_name: DashboardSummary.tsx
 * @description: v2.1.1 — top-of-page summary strip. Doubles as the color
 * legend so users learn what the rail colors mean by reading this once.
 *
 * Layout:
 *   3 healthy · 1 warning · 2 paused · 1 error · 4 idle           [tooltip on hover]
 *
 * Each chip is a button that scrolls/highlights matching cards (future);
 * for now they're informational + serve as the legend.
 */
import type { AgentStatus, AgentHealth } from '@/types';

interface ChipDef {
  health: AgentHealth;
  label: string;
  dotCls: string;
  textCls: string;
}

const CHIP_ORDER: ChipDef[] = [
  { health: 'healthy_running', label: 'running',  dotCls: 'bg-emerald-500', textCls: 'text-emerald-600 dark:text-emerald-400' },
  { health: 'healthy_idle',    label: 'idle',     dotCls: 'bg-sky-500',     textCls: 'text-sky-600 dark:text-sky-400' },
  { health: 'warning',         label: 'blocked',  dotCls: 'bg-amber-500',   textCls: 'text-amber-600 dark:text-amber-400' },
  { health: 'paused',          label: 'paused',   dotCls: 'bg-yellow-500',  textCls: 'text-yellow-600 dark:text-yellow-400' },
  { health: 'error',           label: 'error',    dotCls: 'bg-red-500',     textCls: 'text-red-600 dark:text-red-400' },
  { health: 'idle_long',       label: 'quiet',    dotCls: 'bg-gray-400',    textCls: 'text-gray-500' },
];

const TOOLTIPS: Record<AgentHealth, string> = {
  healthy_running: 'Agent is actively working — no errors, no warnings',
  healthy_idle:    'Agent is idle but was active recently',
  idle_long:       'Agent has been idle for more than 72 hours',
  warning:         'Agent has at least one job blocked by dependencies',
  paused:          'Agent has at least one job paused by user',
  error:           'Agent has a failed job or recent error event',
};

export function DashboardSummary({ agents }: { agents: AgentStatus[] }) {
  // Count by health. Public agents don't have `health` field; treat as healthy_idle.
  const counts: Record<AgentHealth, number> = {
    healthy_running: 0,
    healthy_idle: 0,
    idle_long: 0,
    warning: 0,
    paused: 0,
    error: 0,
  };
  for (const a of agents) {
    const h: AgentHealth = a.owned_by_viewer ? a.health : 'healthy_idle';
    counts[h] += 1;
  }
  const total = agents.length;
  if (total === 0) return null;

  return (
    <div
      data-testid="dashboard-summary"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs rounded-lg border border-[var(--border-primary)] bg-[var(--bg-glass)] px-3 py-2"
    >
      <span className="font-semibold text-[var(--text-secondary)]">{total} agent{total === 1 ? '' : 's'}</span>
      <span className="text-[var(--border-primary)]">·</span>
      {CHIP_ORDER.map((c) => {
        const n = counts[c.health];
        if (n === 0) return null;
        return (
          <span
            key={c.health}
            title={TOOLTIPS[c.health]}
            className={`inline-flex items-center gap-1 ${c.textCls}`}
          >
            <span className={`inline-block h-2 w-2 rounded-full ${c.dotCls}`} aria-hidden />
            {n} {c.label}
          </span>
        );
      })}
      <span className="ml-auto text-[10px] text-[var(--text-secondary)] italic">
        hover any colored dot for meaning
      </span>
    </div>
  );
}
