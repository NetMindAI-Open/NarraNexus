/**
 * Arena landing UI state.
 *
 * Decouples the (async, ~0.7s) Arena provisioning from page render: the app
 * loads normally and a small non-blocking modal reflects this status, instead
 * of the whole page sitting on a full-screen spinner.
 */
import { create } from 'zustand';

export type ArenaLandingStatus = 'idle' | 'provisioning' | 'ready' | 'error';

interface ArenaLandingState {
  status: ArenaLandingStatus;
  arenaName?: string;
  error?: string;
  setProvisioning: () => void;
  setReady: (arenaName?: string) => void;
  setError: (error: string) => void;
  reset: () => void;
}

export const useArenaLandingStore = create<ArenaLandingState>((set) => ({
  status: 'idle',
  arenaName: undefined,
  error: undefined,
  setProvisioning: () => set({ status: 'provisioning', error: undefined }),
  setReady: (arenaName) => set({ status: 'ready', arenaName }),
  setError: (error) => set({ status: 'error', error }),
  reset: () => set({ status: 'idle', arenaName: undefined, error: undefined }),
}));
