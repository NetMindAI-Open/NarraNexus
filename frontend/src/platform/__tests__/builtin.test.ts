/**
 * @file_name: builtin.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The shell's builtin registrations reproduce the expected route table, sidebar, panels and settings nav.
 *
 * `golden/routes.expected.json` (M-8) is a route-table CHANGE DETECTOR, not a frozen historical
 * snapshot: one row per registered page — path order, layout, guard, welcome-gate skip, whether
 * the element is a lazy page / an inline component / a layout-owned `null`, and for lazy pages
 * WHICH page module it loads (`module`, read off the lazy loader — see `rowFor`). It is
 * hand-maintained and gets updated whenever a builtin page is deliberately added, removed,
 * reordered or re-pointed. Its value is that an UNINTENDED change to `PAGES` (registration order
 * shuffled, a path silently renamed, a route wired to the wrong page, an entry dropped from
 * `builtin.ts`) shows up here as a diff to review, rather than as a shipped regression only later
 * discovered by users navigating a dead link. Not in the golden, still hardcoded in `AppRoutes`:
 * the `/app` layout route itself, its `index` redirect, `/` and `*`.
 */
import { describe, expect, it } from 'vitest';

import '@/platform/builtin';
import '@/pages/settings/registerBuiltinSections';
import { PAGES, PANELS, SETTINGS_SECTIONS, SIDEBAR, TOOL_RENDERERS, sortedSettingsSections, sortedSidebarItems, type PageDef } from '@/platform/registries';
import { builtinTabIds } from '@/components/bookmarks/tabs';
import { BUILTIN_TAB_IDS } from '@/components/bookmarks/builtinTabIds';
import {
  ArtifactsTab,
  AwarenessTab,
  BrowserTab,
  BuilderTab,
  ChannelsTab,
  InboxTab,
  JobsTab,
  McpTab,
  MemoryTab,
  SkillsTab,
  SmartHomeTab,
  SocialTab,
  WorkspaceTab,
} from '@/platform/builtinPanels';
import expected from './golden/routes.expected.json';

interface RouteRow {
  path: string;
  layout: 'top' | 'app';
  guard: string;
  element: 'lazy' | 'component' | null;
  skipWelcomeGate?: boolean;
  /** The page module a lazy element loads (`pages/<Name>`), so a route wired to the wrong page fails here. */
  module?: string;
}

/**
 * The module a `React.lazy` element will import. Before its first render the
 * lazy payload still holds the loader thunk, whose source text names the
 * import (`() => import("/src/pages/DashboardPage.tsx")` after Vite's
 * transform). Reading React's `_payload` is test-only introspection; if a
 * React upgrade changes it this test fails loudly, it cannot go silently green.
 */
function lazyModuleOf(el: unknown): string | undefined {
  const payload = (el as { _payload?: { _status?: number; _result?: unknown } })._payload;
  if (!payload || payload._status !== -1) return undefined;
  const match = /\/src\/(pages\/[A-Za-z0-9_]+)/.exec(String(payload._result));
  return match?.[1];
}

function rowFor(def: PageDef): RouteRow {
  const el = def.element;
  const element: RouteRow['element'] =
    el === null ? null : typeof el === 'object' && '$$typeof' in el ? 'lazy' : 'component';
  const row: RouteRow = { path: def.path, layout: def.layout, guard: def.guard, element };
  if (def.skipWelcomeGate) row.skipWelcomeGate = true;
  if (element === 'lazy') row.module = lazyModuleOf(el);
  return row;
}

describe('builtin pages', () => {
  it('the registered route table matches golden/routes.expected.json (order, guard, layout, gate, element kind, page module)', () => {
    const top = PAGES.list().filter((e) => e.value.layout === 'top').map((e) => rowFor(e.value));
    const app = PAGES.list().filter((e) => e.value.layout === 'app').map((e) => rowFor(e.value));
    expect([...top, ...app]).toEqual(expected as RouteRow[]);
  });

  it('the two import routes share ONE lazy component (no remount when navigating between them)', () => {
    expect(PAGES.get('templates-install')!.element).toBe(PAGES.get('bundle-import')!.element);
  });

  it('guards match the old wrappers', () => {
    const guard = (id: string) => PAGES.get(id)?.guard;
    expect(guard('login')).toBe('public');
    expect(guard('nm-playground')).toBe('open');
    // dev #383: /setup forwards to /welcome; the welcome flow and /pay skip the first-run gate
    expect(guard('setup')).toBe('open');
    expect(guard('welcome')).toBe('protected');
    expect(PAGES.get('welcome')?.skipWelcomeGate).toBe(true);
    expect(guard('pay')).toBe('protected');
    expect(PAGES.get('pay')?.skipWelcomeGate).toBe(true);
    expect(guard('agents-new')).toBe('protected');
    expect(guard('agent-profile')).toBe('protected');
    expect(PAGES.get('chat')?.element).toBeNull();
    expect(PAGES.get('team-chat')?.element).toBeNull();
  });
});

describe('builtin sidebar', () => {
  it('lists the six rows in the old order and hides System when the feature is off', () => {
    const all = sortedSidebarItems({ showSystemPage: true }).map((e) => e.id);
    expect(all).toEqual(['export', 'dashboard', 'marketplace', 'workspace', 'settings', 'system']);
    expect(sortedSidebarItems({ showSystemPage: false }).map((e) => e.id)).not.toContain('system');
  });

  it('dashboard and export rows are active by pathname plus the ?tab query', () => {
    const exportRow = SIDEBAR.get('export')!;
    const dashRow = SIDEBAR.get('dashboard')!;
    const at = (pathname: string, search = '') => ({ pathname, search });
    expect(exportRow.isActive!(at('/app/dashboard', '?tab=export'))).toBe(true);
    expect(dashRow.isActive!(at('/app/dashboard', '?tab=export'))).toBe(false);
    expect(dashRow.isActive!(at('/app/dashboard'))).toBe(true);
    expect(exportRow.isActive!(at('/app/settings'))).toBe(false);
  });
});

describe('builtin panels', () => {
  it('every builtin rail tab has a panel component', () => {
    for (const id of BUILTIN_TAB_IDS) expect(PANELS.get(id)?.component, id).toBeTruthy();
  });

  it('the registry-derived builtin strip tabs are exactly the shell-authored BUILTIN_TAB_IDS', () => {
    const featureTabs = PANELS.list()
      .filter((entry) => entry.owner === 'builtin.browser' && entry.value.strip)
      .map((entry) => entry.id);
    expect([...builtinTabIds(), ...featureTabs].sort()).toEqual([...BUILTIN_TAB_IDS].sort());
  });

  it('each rail tab maps to its OWN builtin panel component, not a mismatched one', () => {
    // A specific identity check, not just truthy: two rail tabs pointed at the
    // same (or a swapped) panel component would pass the "truthy" check above.
    expect(PANELS.get('builder')?.component).toBe(BuilderTab);
    expect(PANELS.get('awareness')?.component).toBe(AwarenessTab);
    expect(PANELS.get('workspace')?.component).toBe(WorkspaceTab);
    expect(PANELS.get('channels')?.component).toBe(ChannelsTab);
    expect(PANELS.get('smarthome')?.component).toBe(SmartHomeTab);
    expect(PANELS.get('social')?.component).toBe(SocialTab);
    expect(PANELS.get('jobs')?.component).toBe(JobsTab);
    expect(PANELS.get('browser')?.component).toBe(BrowserTab);
    expect(PANELS.get('inbox')?.component).toBe(InboxTab);
    expect(PANELS.get('artifacts')?.component).toBe(ArtifactsTab);
    expect(PANELS.get('skills')?.component).toBe(SkillsTab);
    expect(PANELS.get('mcp')?.component).toBe(McpTab);
    expect(PANELS.get('memory')?.component).toBe(MemoryTab);
  });

  it('browser_look output is owned by builtin.browser, so disabling the browser removes the card too', () => {
    const entry = TOOL_RENDERERS.list().find((e) => e.id === 'browser_look');
    expect(entry?.owner).toBe('builtin.browser');
    expect(entry?.value.accepts?.('{"outcome": "OK"}')).toBe(true);
    expect(entry?.value.accepts?.('not a look')).toBe(false);
  });
});

describe('builtin settings sections', () => {
  it('keeps the old nav order and visibility gates', () => {
    const ids = (opts: { isTauri: boolean; isCloud: boolean }) => sortedSettingsSections(opts).map((e) => e.id);
    expect(ids({ isTauri: true, isCloud: false })).toEqual([
      // 'browser' sits between artifacts and privacy (order 55) and is gated
      // on NEITHER surface: the agent's "install the browser" refusal can be
      // seen on web and desktop alike, so the destination exists on both.
      'account', 'providers', 'modeldefaults', 'plugins', 'artifacts', 'browser', 'privacy', 'personalization', 'updates',
    ]);
    expect(ids({ isTauri: false, isCloud: true })).toEqual([
      'account', 'providers', 'modeldefaults', 'artifacts', 'browser', 'privacy', 'personalization',
    ]);
    expect(SETTINGS_SECTIONS.get('account')?.neverDefault).toBe(true);
  });
});
