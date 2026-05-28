/**
 * @file_name: updaterStore.ts
 * @description: Frontend mirror of the Rust auto-updater state machine.
 *
 * One source of truth (Rust `AppState.updater_state`) feeds three UI
 * surfaces (global banner / Settings panel / tray menu label). This
 * store is the bridge for the two frontend surfaces — the tray label
 * is wired Rust-side directly. See `tauri/src-tauri/src/commands/updater.rs`
 * for the state machine and `App.tsx` for where the listener mounts.
 *
 * Transition graph (mirror of Rust enum):
 *
 *   idle → checking → up_to_date
 *                  → available → downloading → installing → ready
 *                  → failed (from any stage)
 *
 * `init()` MUST be called exactly once at App mount. It:
 *   - pulls the current state via `updater_get_state` (covers the case
 *     where a startup-auto pipeline already transitioned past idle
 *     before React mounted), then
 *   - subscribes to the live `updater:state` event so future
 *     transitions arrive without polling.
 */

import { create } from 'zustand';
import { isTauri, getUpdaterState, listenUpdaterState } from '@/lib/tauri';

export type UpdaterState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'up_to_date'; current: string; checked_at: number }
  | { kind: 'available'; version: string; notes: string | null }
  | {
      kind: 'downloading';
      downloaded: number;
      total: number | null;
      percent: number | null;
    }
  | { kind: 'installing'; version: string }
  | { kind: 'ready'; version: string }
  | { kind: 'failed'; stage: 'check' | 'download' | 'install'; error: string };

interface UpdaterStore {
  state: UpdaterState;
  /** Was init() already called? Guards against double-mount in dev StrictMode. */
  initialised: boolean;
  /** Bound to the unsubscribe returned by `listenUpdaterState`. */
  unlisten: (() => void) | null;
  setState: (s: UpdaterState) => void;
  init: () => Promise<void>;
  /** Idempotent teardown — safe to call from React effect cleanup. */
  teardown: () => void;
}

export const useUpdaterStore = create<UpdaterStore>((set, get) => ({
  state: { kind: 'idle' },
  initialised: false,
  unlisten: null,
  setState: (s) => set({ state: s }),
  init: async () => {
    if (get().initialised) return;
    set({ initialised: true });
    if (!isTauri()) return; // web/cloud build: no updater at all
    // Pull the snapshot BEFORE subscribing so a fast startup-pipeline
    // transition that happened before listener attached is not missed.
    try {
      const snapshot = await getUpdaterState();
      if (snapshot) set({ state: snapshot });
    } catch (e) {
      console.warn('[updaterStore] failed to fetch initial state:', e);
    }
    try {
      const unlisten = await listenUpdaterState((next) => {
        set({ state: next });
      });
      set({ unlisten });
    } catch (e) {
      console.warn('[updaterStore] failed to subscribe to updater:state:', e);
    }
  },
  teardown: () => {
    const { unlisten } = get();
    if (unlisten) {
      try {
        unlisten();
      } catch {
        /* listener already gone */
      }
    }
    set({ initialised: false, unlisten: null });
  },
}));
