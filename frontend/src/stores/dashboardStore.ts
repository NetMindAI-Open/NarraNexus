/**
 * @file_name: dashboardStore.ts
 * @author: NexusAgent
 * @date: 2026-04-13
 * @description: Zustand store for /dashboard page. Manages agent list state,
 * polling FSM (visibility × tauri focus × any_running), and 429 backoff.
 *
 * FSM (design TDR-6):
 *   hidden          → ∞ (pause)
 *   tauri blurred   → ∞ (pause)
 *   visible+running → 3000ms
 *   visible+idle    → 30000ms
 *   429 in-flight   → backoff 2s..60s (exponential)
 */
import { create } from 'zustand';
import type { AgentStatus } from '@/types';
import { isTauri } from '@/lib/tauri';

export interface DashboardState {
  agents: AgentStatus[];
  isLoading: boolean;
  error: string | null;
  visibility: boolean;
  tauriFocused: boolean;
  backoffMs: number;
  lastTrayCount: number;

  setAgents: (a: AgentStatus[]) => void;
  setVisibility: (v: boolean) => void;
  setTauriFocused: (f: boolean) => void;
  computeInterval: () => number;
  computeRunningCount: () => number;
  onRateLimited: () => void;
  onFetchSuccess: (agents: AgentStatus[]) => void;
  onFetchError: (msg: string) => void;
  reset: () => void;
}

const INTERVAL_RUNNING = 3000;
const INTERVAL_IDLE = 30_000;
const BACKOFF_MIN = 2000;
const BACKOFF_MAX = 60_000;

export const useDashboardStore = create<DashboardState>((set, get) => ({
  agents: [],
  isLoading: false,
  error: null,
  visibility: true,
  // In web mode there is no Tauri window, so "focused" is always true.
  tauriFocused: true,
  backoffMs: 0,
  lastTrayCount: 0,

  setAgents: (a) => set({ agents: a }),
  setVisibility: (v) => set({ visibility: v }),
  setTauriFocused: (f) => set({ tauriFocused: f }),

  computeInterval: () => {
    const { visibility, tauriFocused, backoffMs, agents } = get();
    if (!visibility) return Infinity;
    if (isTauri() && !tauriFocused) return Infinity;
    if (backoffMs > 0) return backoffMs;
    const anyRunning = agents.some((a) => a.status.kind !== 'idle');
    return anyRunning ? INTERVAL_RUNNING : INTERVAL_IDLE;
  },

  computeRunningCount: () =>
    get().agents.filter((a) => a.status.kind !== 'idle').length,

  onRateLimited: () => {
    const prev = get().backoffMs;
    const next = prev === 0 ? BACKOFF_MIN : Math.min(BACKOFF_MAX, prev * 2);
    set({ backoffMs: next });
  },

  onFetchSuccess: (agents) =>
    set({ agents, error: null, backoffMs: 0, isLoading: false }),

  onFetchError: (msg) => set({ error: msg, isLoading: false }),

  reset: () =>
    set({
      agents: [],
      isLoading: false,
      error: null,
      visibility: true,
      tauriFocused: true,
      backoffMs: 0,
      lastTrayCount: 0,
    }),
}));
