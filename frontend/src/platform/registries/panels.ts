/**
 * @file_name: panels.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Drawer panel registry — the right-hand rail tabs and the component each renders.
 *
 * `bookmarks/tabs.ts` derives the strip layout (category grouping, sort
 * order) from these entries so `BookmarkPanelHost` dispatches by lookup
 * instead of an `&&` chain, and a plugin can add a panel AND its strip
 * entry from one `PANELS.register(...)` call — see `strip` below, and
 * `bookmarks/tabs.ts` for why category branding stays a small separate
 * table rather than another per-entry field.
 */
import type { ComponentType, LazyExoticComponent } from 'react';
import type { LucideIcon } from 'lucide-react';

import { Registry } from './registry';

export interface PanelProps {
  agentId: string;
}

/** The strip-visible metadata for a panel entry; omitted entirely for a panel that never
 *  appears on the drawer strip (there are none of these today, but the shape allows it —
 *  a panel reachable only by direct `requestPanel(id)` call would be one). */
export interface PanelStripDef {
  /** i18n key (namespace `rail`) for the label; `label` is the fallback literal. */
  labelKey: string;
  label: string;
  icon: LucideIcon;
  /** Short caption for the 64px strip when the label is too long. */
  stripLabel?: string;
  stripLabelKey?: string;
  /** Which strip category group this panel's tab appears under (`bookmarks/tabs.ts`'s
   *  `CATEGORY_META` keys: `config` / `activity` / `narra` / `nexus` / `skills`). A plugin
   *  panel with no matching category value falls back to `config`. */
  category?: string;
  /** Sort key within the category; builtins use 10, 20, … so plugins can slot between. */
  order?: number;
  /**
   * Offered only in a specific context (currently just `'studio'`: the
   * creation studio is open — or resumable — on the current agent). The tab
   * stays REGISTERED regardless (so a direct link or `requestPanel(id)`
   * still works); only the pickable lists (chat header ⋯ menu, ⌘K palette)
   * filter on it, via `bookmarks/tabs.ts`'s `visibleTabs(ctx)` — the one
   * place this rule is applied.
   */
  conditional?: 'studio';
}

export interface PanelDef {
  component: LazyExoticComponent<ComponentType<PanelProps>> | ComponentType<PanelProps>;
  /** Strip presence; a panel without this never appears in `bookmarks/tabs.ts`'s derived
   *  strip (registered and directly reachable, but not offered as a rail tab). */
  strip?: PanelStripDef;
}

export const PANELS = new Registry<PanelDef>('ui.panels');
