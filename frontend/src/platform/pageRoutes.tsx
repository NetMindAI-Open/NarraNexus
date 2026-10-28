/**
 * @file_name: pageRoutes.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Turns the PAGES registry into <Route> elements — the one place the route table is built.
 *
 * `App.tsx` renders the two arrays this returns (top-level routes and the
 * children of the /app layout). Keeping the builder out of `App.tsx` makes
 * it testable without mounting the whole shell: a test registers a page,
 * renders the routes in a MemoryRouter and sees the page. The guard
 * wrappers are injected because they live with the shell's auth state.
 *
 * A page that cannot be placed safely is dropped and reported (not thrown):
 * an `/app` page that does not declare `guard: 'protected'`, or any page
 * whose `guard` is not one of the three known values (a plugin manifest is
 * untyped at runtime — an unknown guard must never fall through to "no
 * wrapper"). This function runs inside `App.tsx`'s render body, above the
 * nearest `ChunkErrorBoundary`, so a throw here would white-screen the whole
 * shell over one bad manifest. `loader.registerDeclaredUi` already rejects
 * such entries at registration time (see loader.ts); this check stays as
 * defense in depth for anything that reaches PAGES another way (a plugin's
 * own bundle calling `host.registries.pages.register`). Each bad entry is
 * reported once per page load, not once per render (the table is rebuilt on
 * every render).
 */
import type { ComponentType, ReactElement, ReactNode } from 'react';
import { Route } from 'react-router-dom';

import { reportUiError } from './errorSink';
import type { PageDef, PageGuard, RegistryEntry } from './registries';

const GUARDS: ReadonlySet<PageGuard> = new Set<PageGuard>(['protected', 'public', 'open']);

/** Entries already reported this page load — the route table is rebuilt on every render. */
const reported = new Set<string>();

/** Test hook: forget which bad entries were already reported. */
export function resetPageRouteReports(): void {
  reported.clear();
}

function reject(entry: RegistryEntry<PageDef>, message: string): void {
  const key = `${entry.owner}:${entry.id}:${String(entry.value.guard)}:${entry.value.layout}`;
  if (reported.has(key)) return;
  reported.add(key);
  if (import.meta.env.DEV) console.error(message);
  reportUiError(new Error(message), { kind: 'render', source: entry.owner, context: 'pageRouteElements' });
}

export interface GuardWrappers {
  ProtectedRoute: ComponentType<{ children: ReactNode; skipWelcomeGate?: boolean }>;
  PublicRoute: ComponentType<{ children: ReactNode }>;
}

export interface PageRouteElements {
  /** Routes rendered at the top level, each wrapped per its `guard`. */
  top: ReactElement[];
  /** Children of the protected /app layout (no extra wrapper). */
  app: ReactElement[];
}

function pageElement(def: PageDef): ReactNode {
  return def.element ? <def.element /> : null;
}

function guarded(def: PageDef, wrappers: GuardWrappers): ReactNode {
  const inner = pageElement(def);
  if (def.guard === 'protected') {
    return <wrappers.ProtectedRoute skipWelcomeGate={def.skipWelcomeGate}>{inner}</wrappers.ProtectedRoute>;
  }
  if (def.guard === 'public') return <wrappers.PublicRoute>{inner}</wrappers.PublicRoute>;
  // 'open' — the caller has already rejected every other value.
  return inner;
}

export function pageRouteElements(entries: RegistryEntry<PageDef>[], wrappers: GuardWrappers): PageRouteElements {
  const top: ReactElement[] = [];
  const app: ReactElement[] = [];
  for (const entry of entries) {
    const { id, value } = entry;
    if (!GUARDS.has(value.guard)) {
      // Fail closed: an unknown guard is never "no guard".
      reject(entry, `ui.pages: "${id}" declares unknown guard "${String(value.guard)}" (expected protected | public | open)`);
      continue;
    }
    if (value.layout === 'top') {
      top.push(<Route key={id} path={value.path} element={guarded(value, wrappers)} />);
      continue;
    }
    if (value.guard !== 'protected') {
      reject(entry, `ui.pages: "${id}" is under /app and must declare guard "protected" (got "${value.guard}")`);
      continue;
    }
    app.push(<Route key={id} path={value.path} element={pageElement(value)} />);
  }
  return { top, app };
}
