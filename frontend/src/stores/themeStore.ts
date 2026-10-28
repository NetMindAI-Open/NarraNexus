/**
 * Theme store — Zustand with localStorage persistence.
 *
 * Single source of truth for light/dark/system theme across the app.
 * Replaces a former useState-based hook whose per-component state did not
 * sync across call sites (toggle in ThemeToggle didn't update Sidebar's
 * isDark, so logo image src went stale after the first toggle).
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type Theme = 'light' | 'dark' | 'system';

function getSystemTheme(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function resolve(theme: Theme): 'light' | 'dark' {
  return theme === 'system' ? getSystemTheme() : theme;
}

interface ThemeState {
  theme: Theme;
  effectiveTheme: 'light' | 'dark';
  /** Id of the plugin theme (`ui.themes` registry) layered over the base theme, or null. */
  pluginTheme: string | null;
  setTheme: (theme: Theme) => void;
  setPluginTheme: (id: string | null) => void;
  toggleTheme: () => void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      theme: 'system',
      effectiveTheme: getSystemTheme(),
      pluginTheme: null,

      setTheme: (theme) => set({ theme, effectiveTheme: resolve(theme) }),
      setPluginTheme: (id) => set({ pluginTheme: id }),

      toggleTheme: () => {
        const { theme } = get();
        const next: Theme = theme === 'light' ? 'dark' : theme === 'dark' ? 'system' : 'light';
        set({ theme: next, effectiveTheme: resolve(next) });
      },
    }),
    {
      name: 'narra-nexus-theme',
      partialize: (state) => ({ theme: state.theme, pluginTheme: state.pluginTheme }),
      onRehydrateStorage: () => (state) => {
        if (state) {
          state.effectiveTheme = resolve(state.theme);
        }
      },
    }
  )
);

// Global OS theme listener: when user is on 'system', follow OS changes live.
if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (useThemeStore.getState().theme === 'system') {
      useThemeStore.setState({ effectiveTheme: getSystemTheme() });
    }
  });
}
