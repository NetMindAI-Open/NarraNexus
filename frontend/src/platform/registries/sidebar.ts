/**
 * @file_name: sidebar.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Sidebar navigation registry — the rows of the left rail as data.
 *
 * Replaces the inline `<button>` list in `Sidebar.tsx`. `to` is the route,
 * `isActive` decides highlighting from the current location (the dashboard
 * row needs the `?tab=` query, so it is a predicate, not a path equality),
 * `visible` gates on runtime features (the System row is local-only).
 */
import type { LucideIcon } from 'lucide-react';

import { Registry, type RegistryEntry } from './registry';

export interface SidebarLocation {
  pathname: string;
  search: string;
}

export interface SidebarFeatures {
  showSystemPage: boolean;
}

export interface SidebarItemDef {
  labelKey: string;
  icon: LucideIcon;
  to: string;
  /** Sort key; builtin rows use 10, 20, … so plugins can slot between. */
  order: number;
  titleKey?: string;
  helpId?: string;
  isActive?: (loc: SidebarLocation) => boolean;
  visible?: (features: SidebarFeatures) => boolean;
  /** Optional prefetch on hover/focus (the dashboard chunk is large). */
  prefetch?: () => void;
}

export const SIDEBAR = new Registry<SidebarItemDef>('ui.sidebar');

/**
 * The rows to draw, filtered by feature gates and sorted by `order`. Pass
 * the entries from `useRegistryEntries(SIDEBAR)` inside a component so the
 * list follows late registrations; the default reads the registry directly.
 */
export function sortedSidebarItems(features: SidebarFeatures, entries: RegistryEntry<SidebarItemDef>[] = SIDEBAR.list()) {
  return entries
    .filter((e) => e.value.visible?.(features) ?? true)
    .sort((a, b) => a.value.order - b.value.order);
}
