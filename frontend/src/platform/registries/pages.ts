/**
 * @file_name: pages.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Page registry — every route under /app/* (and the public/top-level ones) as data.
 *
 * `App.tsx` renders `<Route>` elements from `PAGES.list()` instead of a
 * hardcoded JSX table; a plugin contributes a page by registering an entry
 * whose `element` is a lazy component. `guard` picks the wrapper the shell
 * applies (`protected` = requires login, `public` = redirect when logged
 * in, `open` = no wrapper); `layout` places the page inside the /app
 * MainLayout outlet or at the top level.
 */
import type { ComponentType, LazyExoticComponent } from 'react';

import { Registry } from './registry';

export type PageGuard = 'protected' | 'public' | 'open';
export type PageLayout = 'app' | 'top';

/** A page renders with no props from the router; optional props are allowed. */
export type PageComponent = ComponentType<Record<string, never>>;

export interface PageDef {
  /** Route path: relative under /app for `layout: 'app'`, absolute for `'top'`. */
  path: string;
  element: LazyExoticComponent<PageComponent> | PageComponent | null;
  /**
   * Wrapper the shell applies to a `layout: 'top'` page. Pages under the
   * /app layout inherit the layout's own ProtectedRoute and MUST declare
   * `'protected'` (the route builder rejects anything else so a plugin
   * cannot believe it published a public page under /app).
   */
  guard: PageGuard;
  layout: PageLayout;
  /** `protected` pages only: skip the first-run welcome gate (the welcome flow itself, /pay). */
  skipWelcomeGate?: boolean;
}

export const PAGES = new Registry<PageDef>('ui.pages');
