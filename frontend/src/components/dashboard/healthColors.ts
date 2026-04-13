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

export const HEALTH_COLORS: Record<AgentHealth, HealthColors> = {
  healthy_running: {
    rail: 'bg-emerald-500',
    cardTint: '',
    text: 'text-emerald-600 dark:text-emerald-400',
    accent: 'bg-emerald-500',
  },
  healthy_idle: {
    rail: 'bg-sky-500',
    cardTint: '',
    text: 'text-sky-600 dark:text-sky-400',
    accent: 'bg-sky-500',
  },
  idle_long: {
    rail: 'bg-gray-400',
    cardTint: 'opacity-75',
    text: 'text-gray-500',
    accent: 'bg-gray-400',
  },
  warning: {
    rail: 'bg-amber-500',
    cardTint: '',
    text: 'text-amber-600 dark:text-amber-400',
    accent: 'bg-amber-500',
  },
  paused: {
    rail: 'bg-yellow-500',
    cardTint: '',
    text: 'text-yellow-600 dark:text-yellow-400',
    accent: 'bg-yellow-500',
  },
  error: {
    rail: 'bg-red-500',
    cardTint: 'bg-red-500/5',
    text: 'text-red-600 dark:text-red-400',
    accent: 'bg-red-500',
  },
};
