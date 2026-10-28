/**
 * @file_name: masterDetailNav.ts
 * @date: 2026-09-10
 * @description: The single definition of the responsive master–detail nav
 * shared by SettingsPage and DashboardPage.
 *
 * Both pages render the same shape: a left rail of section buttons next to
 * a detail pane. At md+ the rail is a fixed 224px column; below md a fixed
 * column ate most of a 360px viewport (GitHub #130), so the wrapper stacks
 * vertically and the rail becomes a horizontal scroll strip above the
 * content. Keeping the class strings here is what keeps the two pages
 * identical: change the breakpoint or rail width once, both follow.
 */
import { cn } from '@/lib/utils';

/** Wrapper holding the rail and the detail pane. */
export const MASTER_DETAIL_ROW_CLASS = 'flex flex-1 min-h-0 flex-col md:flex-row';

/** The `<nav>` rail: horizontal strip below md, fixed column at md+. */
export const MASTER_NAV_CLASS =
  'flex shrink-0 gap-1 overflow-x-auto border-b px-3 py-2 md:block md:w-56 md:gap-0 md:overflow-x-visible md:overflow-y-auto md:space-y-1 md:border-b-0 md:border-r md:px-3 md:py-4';

/** Base classes of one rail button (without the active/inactive colours). */
export const MASTER_NAV_ITEM_CLASS =
  'shrink-0 flex items-center gap-2.5 px-3 py-2 rounded-[var(--radius-lg)] text-sm text-left transition-colors md:w-full';

/** Full class list of one rail button in its active or inactive state. */
export function masterNavItemClass(isActive: boolean): string {
  return cn(
    MASTER_NAV_ITEM_CLASS,
    isActive
      ? 'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] font-medium'
      : 'text-[var(--nm-ink70)] hover:bg-[var(--nm-line)]/40 hover:text-[var(--nm-ink)]',
  );
}
