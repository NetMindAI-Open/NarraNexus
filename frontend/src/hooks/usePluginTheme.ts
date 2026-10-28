/**
 * @file_name: usePluginTheme.ts
 * @author: Bin Liang
 * @date: 2026-09-08
 * @description: Applies the chosen plugin theme (ui.themes registry) to the document root, and keeps it applied as plugins register late.
 *
 * The theme id lives in `themeStore.pluginTheme` (persisted); the tokens live
 * in the `THEMES` registry, which a plugin fills only once it is activated —
 * possibly well after first paint. Subscribing to both means a persisted
 * choice is re-applied the moment its plugin registers, and cleared when the
 * plugin is unloaded or the choice is reset.
 */
import { useEffect } from 'react';

import { THEMES, applyTheme, clearTheme, useRegistryEntries } from '@/platform/registries';
import { useThemeStore } from '@/stores/themeStore';

export function usePluginTheme(): void {
  const pluginTheme = useThemeStore((s) => s.pluginTheme);
  const registered = useRegistryEntries(THEMES);
  const available = pluginTheme !== null && registered.some((e) => e.id === pluginTheme);
  useEffect(() => {
    if (pluginTheme !== null && available) applyTheme(pluginTheme);
    else clearTheme();
    return () => clearTheme();
  }, [pluginTheme, available]);
}
