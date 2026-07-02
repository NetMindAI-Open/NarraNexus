/**
 * @file_name: healthColors.ts
 * @description: v2.1 — map AgentHealth to Tailwind classes for status rail,
 * card tint, and sparkline color. Centralized so swapping the palette is
 * one-file.
 */
import type { AgentHealth } from '@/types';

export interface HealthColors {
  rail: string;       // Tailwind class for the 4px left rail
  cardTint: string;   // optional subtle card-body background class (empty string = none)
  text: string;       // for verb/metric text emphasis
  accent: string;     // sparkline + badges
}

// v2.2 G4: rail uses vertical gradient + inset shadow for a soft glow,
// instead of a flat 4px color band. Keeps the same primary hue per state
// so existing "bg-emerald" / "bg-red" regex assertions still match.
export const HEALTH_COLORS: Record<AgentHealth, HealthColors> = {
  healthy_running: {
    rail: 'bg-gradient-to-b from-emerald-400 to-emerald-600 shadow-[inset_-1px_0_2px_rgba(16,185,129,0.45)]',
    cardTint: '',
    text: 'text-emerald-600 dark:text-emerald-400',
    accent: 'bg-emerald-500',
  },
  // idle: brand SILICON rail/accent (the agent species colour), but the status
  // TEXT is neutral ink — the coloured rail already signals "idle", so the
  // "Idle · last active …" line reads as plain text, not blue.
  healthy_idle: {
    rail: 'bg-[color:var(--color-silicon)] shadow-[inset_-1px_0_3px_var(--color-silicon-hair)]',
    cardTint: '',
    text: 'text-[color:var(--text-primary)]',
    accent: 'bg-[color:var(--color-silicon)]',
  },
  idle_long: {
    rail: 'bg-gradient-to-b from-gray-300 to-gray-500',
    cardTint: 'opacity-75',
    text: 'text-gray-500',
    accent: 'bg-gray-400',
  },
  warning: {
    rail: 'bg-gradient-to-b from-amber-400 to-amber-600 shadow-[inset_-1px_0_2px_rgba(245,158,11,0.45)]',
    cardTint: '',
    text: 'text-amber-600 dark:text-amber-400',
    accent: 'bg-amber-500',
  },
  paused: {
    rail: 'bg-gradient-to-b from-yellow-400 to-yellow-600',
    cardTint: '',
    text: 'text-yellow-600 dark:text-yellow-400',
    accent: 'bg-yellow-500',
  },
  error: {
    rail: 'bg-gradient-to-b from-red-400 to-red-600 shadow-[inset_-1px_0_2px_rgba(239,68,68,0.45)]',
    cardTint: 'bg-red-500/5',
    text: 'text-red-600 dark:text-red-400',
    accent: 'bg-red-500',
  },
  // v2.2 G2: error fully-acknowledged. Neutral slate rail; the small red
  // ack dot rendered by AgentCard signals "user saw it but not fixed".
  // Security-M1: error MUST NOT visually downgrade to healthy.
  acknowledged: {
    rail: 'bg-gradient-to-b from-slate-400 to-slate-600',
    cardTint: '',
    text: 'text-slate-600 dark:text-slate-400',
    accent: 'bg-slate-500',
  },
};

import type { AgentKind } from '@/types';

/**
 * v2.2 G2: derive the health to render given server-side health and the
 * dismiss state of all attention banners.
 *
 * Security invariant (S-M1): error NEVER downgrades to healthy. An error
 * that the user dismissed banners for is still an error in the data layer;
 * the UI renders a neutral "acknowledged" rail + a small red dot to signal
 * "you saw it, it's not fixed". warning/paused DO downgrade — they are
 * lower-severity (warning) or user-initiated (paused).
 */
export function acknowledgedHealthOf(
  health: AgentHealth,
  allDismissed: boolean,
  kind: AgentKind,
): AgentHealth {
  if (!allDismissed) return health;
  if (health === 'error') return 'acknowledged';
  if (health === 'warning' || health === 'paused') {
    return kind === 'idle' ? 'healthy_idle' : 'healthy_running';
  }
  return health;
}
