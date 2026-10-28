/**
 * @file_name: hooks.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: React subscription to a registry — components re-render when a plugin registers late.
 */
import { useSyncExternalStore } from 'react';

import type { Registry, RegistryEntry } from './registry';

/**
 * The registry's entries as a stable snapshot; a new array only when the
 * registry changed (useSyncExternalStore requires referential stability).
 */
export function useRegistryEntries<T>(registry: Registry<T>): RegistryEntry<T>[] {
  return useSyncExternalStore(
    (cb) => registry.subscribe(cb),
    () => registry.snapshot(),
    () => registry.snapshot(),
  );
}
