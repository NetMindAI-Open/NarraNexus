/**
 * @file_name: loader.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The loader registers gates from manifest metadata, activates a plugin through fetch+SRI+import, and the gate renders the plugin's page after activation.
 */
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fireActivation, registerActivation, resetActivation } from '@/platform/activation';
import { onUiError, resetErrorSink } from '@/platform/errorSink';
import { activatePlugin, activeHosts, assetUrl, deactivatePlugin, loadPlugins, registerDeclaredUi, type FactoryPluginRow, type PluginModule } from '@/platform/loader';
import { COMMANDS, PAGES, PANELS, isOwnerDisabled } from '@/platform/registries';

const ROW: FactoryPluginRow = {
  id: 'acme.weather',
  version: '1.0.0',
  enabled: true,
  loaded: true,
  state: 'enabled',
  frontend: {
    entry: 'plugin.js',
    ui: {
      pages: [{ id: 'acme.weather.home', path: 'x/weather' }],
      panels: [{ id: 'acme.weather.panel' }],
      commands: [{ id: 'acme.weather.refresh', label: 'Refresh weather' }],
    },
  },
  activation_events: [],
};

import { createHash } from 'node:crypto';

const digestImpl = async (bytes: ArrayBuffer): Promise<ArrayBuffer> => {
  const out = createHash('sha256').update(new Uint8Array(bytes)).digest();
  return out.buffer.slice(out.byteOffset, out.byteOffset + out.byteLength) as ArrayBuffer;
};

async function sri(text: string): Promise<string> {
  const digest = await digestImpl(new TextEncoder().encode(text).buffer as ArrayBuffer);
  return 'sha256-' + btoa(String.fromCharCode(...new Uint8Array(digest)));
}

function fakeFetch(body: string, status = 200): typeof fetch {
  return vi.fn(async () => new Response(body, { status })) as unknown as typeof fetch;
}

afterEach(() => {
  resetActivation();
  resetErrorSink();
  for (const id of ['acme.weather.home', 'acme.weather.panel', 'acme.weather.refresh', 'acme.weather.wave']) {
    for (const reg of [PAGES, PANELS, COMMANDS]) {
      const entry = reg.list().find((e) => e.id === id);
      if (entry) (reg as { register: typeof PAGES.register }).register(id, entry.value as never, { owner: entry.owner, replace: true })();
    }
  }
  deactivatePlugin('acme.weather');
});

describe('loader', () => {
  it('assetUrl rejects a `..` segment or any character outside the safe set', () => {
    expect(() => assetUrl('acme.weather', '../../etc/passwd')).toThrow(/unsafe entry/);
    expect(() => assetUrl('acme.weather', 'plugin.js?x=%2e%2e')).toThrow(/unsafe entry/);
    expect(assetUrl('acme.weather', 'plugin.js')).toContain('plugin.js');
  });

  it('registers gates for declared pages/panels/commands under the plugin owner', () => {
    const events = registerDeclaredUi(ROW);
    expect(events).toEqual(['onPage:acme.weather.home', 'onPanel:acme.weather.panel', 'onCommand:acme.weather.refresh']);
    expect(PAGES.list().find((e) => e.id === 'acme.weather.home')?.owner).toBe('acme.weather');
    expect(PAGES.get('acme.weather.home')?.path).toBe('x/weather');
    expect(PANELS.has('acme.weather.panel') && COMMANDS.has('acme.weather.refresh')).toBe(true);
  });

  it('rejects (does not register) an /app page manifest that claims a non-protected guard, and reports it', () => {
    const seen = vi.fn();
    const unsubscribe = onUiError(seen);
    const row: FactoryPluginRow = { ...ROW, id: 'acme.leaky', frontend: { entry: 'plugin.js', ui: { pages: [{ id: 'acme.leaky.home', path: 'x/leaky', layout: 'app', guard: 'open' }] } } };
    const events = registerDeclaredUi(row);
    expect(PAGES.has('acme.leaky.home')).toBe(false);
    expect(events).toEqual([]); // the illegal page contributes no activation event either
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0].source).toBe('acme.leaky');
    unsubscribe();
  });

  it('rejects a plugin page path outside the shared x/ namespace, and reports it (M-2)', () => {
    const seen = vi.fn();
    const unsubscribe = onUiError(seen);
    // A static plugin path like "agents/mine" would outrank the builtin dynamic
    // "agents/:agentId" route under React Router's specificity ranking (static beats
    // dynamic) for any agent literally named "mine" — confining plugin pages to x/
    // makes that collision structurally impossible instead of order-dependent.
    const row: FactoryPluginRow = { ...ROW, id: 'acme.rogue', frontend: { entry: 'plugin.js', ui: { pages: [{ id: 'acme.rogue.home', path: 'agents/mine' }] } } };
    const events = registerDeclaredUi(row);
    expect(PAGES.has('acme.rogue.home')).toBe(false);
    expect(events).toEqual([]);
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0].source).toBe('acme.rogue');
    unsubscribe();
    PAGES.removeOwner('acme.rogue');
  });

  it('reports (and does not overwrite) an id collision instead of silently skipping (M-2)', () => {
    registerDeclaredUi(ROW);
    const seen = vi.fn();
    const unsubscribe = onUiError(seen);
    // A second plugin declaring the SAME page id as an already-registered one — the old
    // code's `if (!PAGES.has(id))` guard silently dropped this with no signal at all.
    const row: FactoryPluginRow = { ...ROW, id: 'acme.impostor', frontend: { entry: 'plugin.js', ui: { pages: [{ id: 'acme.weather.home', path: 'x/impostor' }] } } };
    registerDeclaredUi(row);
    expect(PAGES.get('acme.weather.home')?.path).toBe('x/weather'); // untouched — first registrant wins
    expect(PAGES.list().find((e) => e.id === 'acme.weather.home')?.owner).toBe('acme.weather');
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0].source).toBe('acme.impostor');
    unsubscribe();
  });

  it('re-registering the SAME row (same owner, same id) is a silent idempotent no-op, not a reported collision (M-9)', () => {
    // loadPlugins() may legitimately run twice for the same plugin — e.g. once while
    // unauthenticated (401, no rows) and again after login re-fetches the real plugin list, or
    // any other re-run. A plugin re-declaring ITS OWN already-registered page must not be
    // treated as a hostile collision the way a DIFFERENT plugin claiming the same id/path is.
    registerDeclaredUi(ROW);
    const seen = vi.fn();
    const unsubscribe = onUiError(seen);
    const events = registerDeclaredUi(ROW);
    expect(seen).not.toHaveBeenCalled();
    expect(PAGES.get('acme.weather.home')?.path).toBe('x/weather');
    expect(events).toContain('onPage:acme.weather.home');
    unsubscribe();
  });

  it('reports a path collision between two different plugin page ids (M-2)', () => {
    registerDeclaredUi(ROW);
    const seen = vi.fn();
    const unsubscribe = onUiError(seen);
    // Two DIFFERENT page ids (so the id-dedup guard above does not catch this) declaring the
    // SAME path — react-router v6 silently takes the first-registered <Route> for a duplicate
    // path and the second is permanently unreachable dead code.
    const row: FactoryPluginRow = { ...ROW, id: 'acme.squatter', frontend: { entry: 'plugin.js', ui: { pages: [{ id: 'acme.squatter.home', path: 'x/weather' }] } } };
    const events = registerDeclaredUi(row);
    expect(PAGES.has('acme.squatter.home')).toBe(false);
    expect(events).toEqual([]);
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0].source).toBe('acme.squatter');
    unsubscribe();
    PAGES.removeOwner('acme.squatter');
  });

  it('activates through fetch, verifies SRI, imports and calls activate(host)', async () => {
    const source = 'export const plugin = {}';
    const activateSpy = vi.fn();
    const mod: PluginModule = { plugin: { activate: activateSpy } };
    const row = { ...ROW, frontend: { ...ROW.frontend!, integrity: await sri(source) } };
    const host = await activatePlugin(row, { fetchImpl: fakeFetch(source), importImpl: async () => mod, hostVersion: '1.19.0', digestImpl });
    expect(activateSpy).toHaveBeenCalledWith(host);
    expect(host.pluginId).toBe('acme.weather');
    expect(host.http.prefix).toBe('/api/x/acme.weather');
    expect(activeHosts()).toEqual(['acme.weather']);
    expect(deactivatePlugin('acme.weather')).toBe(true);
    expect(activeHosts()).toEqual([]);
  });

  it('refuses a bundle whose integrity does not match', async () => {
    const row = { ...ROW, frontend: { ...ROW.frontend!, integrity: 'sha256-AAAA' } };
    await expect(activatePlugin(row, { fetchImpl: fakeFetch('x'), importImpl: async () => ({}), digestImpl })).rejects.toThrow(/integrity mismatch/);
    await expect(activatePlugin(ROW, { fetchImpl: fakeFetch('x', 404), importImpl: async () => ({}) })).rejects.toThrow(/HTTP 404/);
    await expect(activatePlugin(ROW, { fetchImpl: fakeFetch('x'), importImpl: async () => ({}) })).rejects.toThrow(/no activate/);
  });

  it('refuses to activate a copy-installed plugin bundle missing integrity, and reports it (architecture E3(a))', async () => {
    // "copy" install means the bundle was written into the plugin store by the installer (from
    // a tarball/repo download) — SRI is the only thing that pins the exact bytes an admin
    // approved to the exact bytes that get imported and executed with full plugin privileges. A
    // "link" install points at a local dev checkout the developer is actively editing (SRI would
    // have to be re-verified on every edit, defeating the point of a dev link), so it is exempt.
    const row: FactoryPluginRow = { ...ROW, mode: 'copy', frontend: { ...ROW.frontend!, integrity: undefined } };
    const seen = vi.fn();
    const unsubscribe = onUiError(seen);
    await expect(activatePlugin(row, { fetchImpl: fakeFetch('x'), importImpl: async () => ({ plugin: { activate: vi.fn() } }) })).rejects.toThrow(/missing integrity/);
    registerActivation(row.id, ['onStartup'], () => activatePlugin(row, { fetchImpl: fakeFetch('x'), importImpl: async () => ({ plugin: { activate: vi.fn() } }) }));
    await fireActivation('onStartup');
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0].source).toBe('acme.weather');
    unsubscribe();
  });

  it('activates a link-installed plugin bundle even with no integrity', async () => {
    const row: FactoryPluginRow = { ...ROW, mode: 'link', frontend: { ...ROW.frontend!, integrity: undefined } };
    const activate = vi.fn();
    const host = await activatePlugin(row, { fetchImpl: fakeFetch('x'), importImpl: async () => ({ plugin: { activate } }) });
    expect(activate).toHaveBeenCalledWith(host);
  });

  it('page gate shows loading, then the page the plugin registered during activate', async () => {
    const importImpl = async (): Promise<PluginModule> => ({
      plugin: {
        activate(host) {
          host.register(host.registries.pages, 'acme.weather.home', { path: 'x/weather', element: () => <p>weather page</p>, guard: 'protected', layout: 'app' });
        },
      },
    });
    const list = JSON.stringify({ data: { plugins: [ROW] } });
    const fetchImpl = vi.fn(async (url: string) => new Response(url.endsWith('/api/plugin-factory') ? list : 'export {}', { status: 200 })) as unknown as typeof fetch;
    const rows = await loadPlugins({ fetchImpl, importImpl, digestImpl });
    expect(rows.map((r) => r.id)).toEqual(['acme.weather']);
    const Gate = PAGES.get('acme.weather.home')!.element as React.ComponentType;
    render(<Gate />);
    expect(screen.getByText(/Loading plugin acme.weather/)).toBeInTheDocument();
    await act(async () => {
      await fireActivation('onPage:acme.weather.home');
    });
    expect(await screen.findByText('weather page')).toBeInTheDocument();
    expect(PAGES.list().find((e) => e.id === 'acme.weather.home')?.owner).toBe('acme.weather');
  });

  it('never disables "builtin.ui" — the implicit default owner for every shell registration — no matter what the factory reports for it', async () => {
    // "builtin.ui" is not a toggle-able builtin plugin package: it is the DEFAULT `owner` every
    // shell registration gets when no explicit owner is passed (`registry.ts`). Disabling it
    // would blacklist that default owner forever AND wipe every builtin page/panel/sidebar/
    // command currently registered under it — bricking the whole shell. The factory always
    // reports it enabled+protected, but this guard does not trust that: it is unconditional.
    const list = JSON.stringify({ data: { builtins: [{ id: 'builtin.ui', enabled: false, excluded: false, protected: false }] } });
    const fetchImpl = vi.fn(async () => new Response(list, { status: 200 })) as unknown as typeof fetch;
    await loadPlugins({ fetchImpl });
    expect(isOwnerDisabled('builtin.ui')).toBe(false);
    // A fresh default-owner registration must still succeed afterward.
    const dispose = COMMANDS.register('acme.builtin-ui-guard-check', { label: 'x', run: () => {} });
    expect(COMMANDS.has('acme.builtin-ui-guard-check')).toBe(true);
    dispose();
  });

  it('a failing factory request loads nothing and never throws', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error('offline');
    }) as unknown as typeof fetch;
    expect(await loadPlugins({ fetchImpl })).toEqual([]);
  });

  it('command gate calls the real command once activated, even when the plugin keeps the manifest label', async () => {
    const row: FactoryPluginRow = { ...ROW, frontend: { ...ROW.frontend!, ui: { commands: [{ id: 'acme.weather.wave', label: 'Wave' }] } } };
    const run = vi.fn();
    const importImpl = async (): Promise<PluginModule> => ({
      plugin: {
        activate(host) {
          // The plugin registers under the SAME label the manifest declared — the most
          // natural thing to write, and the exact case the old label-comparison gate got wrong.
          host.register(host.registries.commands, 'acme.weather.wave', { label: 'Wave', run });
        },
      },
    });
    registerDeclaredUi(row);
    const cmd = COMMANDS.get('acme.weather.wave')!;
    await cmd.run();
    expect(run).not.toHaveBeenCalled(); // not activated yet
    const list = JSON.stringify({ data: { plugins: [row] } });
    const fetchImpl = vi.fn(async (url: string) => new Response(url.endsWith('/api/plugin-factory') ? list : 'export {}', { status: 200 })) as unknown as typeof fetch;
    await loadPlugins({ fetchImpl, importImpl, digestImpl });
    await COMMANDS.get('acme.weather.wave')!.run();
    expect(run).toHaveBeenCalledTimes(1);
  });
});
