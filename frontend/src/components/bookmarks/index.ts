/**
 * @file_name: index.ts
 * @date: 2026-06-11
 * @description: Barrel export for the bookmarks component family —
 *   atomic-tab IA: BookmarkStrip (categories + atomic tabs),
 *   BookmarkDrawer (slide-over / pinned shell), BookmarkPanelHost
 *   (one lazy panel per tab), tabs registry.
 */

export { BookmarkStrip } from './BookmarkStrip';
export type { BookmarkStripProps } from './BookmarkStrip';

export { BookmarkDrawer } from './BookmarkDrawer';

export { BookmarkPanelHost } from './BookmarkPanelHost';

export { STRIP_CATEGORIES, ALL_TABS, tabLabel, deriveTabStatus, markTabOpened } from './tabs';
export type { AtomicTabId, AtomicTabDef, StripCategory, TabStatus } from './tabs';
