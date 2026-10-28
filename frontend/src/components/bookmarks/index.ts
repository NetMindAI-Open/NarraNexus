/**
 * @file_name: index.ts
 * @date: 2026-06-11
 * @description: Barrel export for the bookmarks component family —
 *   atomic-tab IA: BookmarkDrawer (slide-over / pinned shell),
 *   BookmarkPanelHost (one lazy panel per tab), tabs registry. The
 *   right-edge BookmarkStrip retired with Chat UI v4 — panel entries
 *   live in the chat header now; the registry stays the single source
 *   of tab ids / labels / icons / status derivation.
 */

export { BookmarkDrawer } from './BookmarkDrawer';
export type { DrawerSwitcherTab, DrawerSwitcherCategory } from './BookmarkDrawer';

export { BookmarkPanelHost } from './BookmarkPanelHost';

export { stripCategories, allTabs, builtinTabIds, visibleTabs, visibleCategories, ArtifactsGlyph, tabLabel, tabLabelKey, tabDescKey, deriveTabStatus, markTabOpened } from './tabs';
export type { AtomicTabId, AtomicTabDef, StripCategory, TabStatus, TabVisibilityContext } from './tabs';
export type { BuiltinTabId } from './builtinTabIds';
