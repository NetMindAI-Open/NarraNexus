/**
 * @file_name: powerStore.ts
 * @author: Bin Liang
 * @date: 2026-07-23
 * @description: Locked Use — keep the computer awake while background
 * automations run (desktop build only).
 *
 * The OS-side sleep assertion lives in the Rust `set_prevent_sleep` command
 * (a `caffeinate -dims -w <pid>` child on macOS, so it can never outlive the
 * app). This store owns the user's intent: the toggle state persists to
 * localStorage and `applyOnStartup()` re-asserts it after a restart, because
 * the previous process's assertion died with it.
 *
 * State is only flipped on a CONFIRMED command result — a failed invoke
 * (e.g. unsupported platform) leaves the toggle off instead of lying.
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { isTauri, invokeTauri } from '@/lib/tauri';

interface PowerState {
  /** User intent: keep the machine awake while the app runs. */
  preventSleep: boolean;

  /** Toggle the OS sleep assertion; resolves after the OS side confirmed. */
  setPreventSleep: (enabled: boolean) => Promise<void>;
  /** Re-assert a persisted "on" state after app start (desktop only). */
  applyOnStartup: () => Promise<void>;
}

export const usePowerStore = create<PowerState>()(
  persist(
    (set, get) => ({
      preventSleep: false,

      setPreventSleep: async (enabled) => {
        if (!isTauri()) return;
        try {
          await invokeTauri('set_prevent_sleep', { enabled });
          set({ preventSleep: enabled });
        } catch (e) {
          console.warn('set_prevent_sleep failed', e);
          set({ preventSleep: false });
        }
      },

      applyOnStartup: async () => {
        if (get().preventSleep) {
          await get().setPreventSleep(true);
        }
      },
    }),
    {
      name: 'narra-nexus-power',
      partialize: (s) => ({ preventSleep: s.preventSleep }),
    }
  )
);
