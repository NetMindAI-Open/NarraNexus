/**
 * @file_name: helloWorld.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The hello-world plugin's real bundle activates through the loader: page, panel, command and theme all land in the registries.
 */
import { act, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';

import { fireActivation, resetActivation } from '@/platform/activation';
import { resetErrorSink } from '@/platform/errorSink';
import { deactivatePlugin, loadPlugins, type FactoryPluginRow, type PluginModule } from '@/platform/loader';
import { COMMANDS, PAGES, PANELS, THEMES } from '@/platform/registries';

const BUNDLE = resolve(__dirname, '../../../../tests/plugins/hello_world/frontend/dist/plugin.js');
const ROW: FactoryPluginRow = {
  id: 'acme.hello_world', version: '1.0.0', enabled: true, loaded: true, state: 'enabled',
  frontend: { entry: 'plugin.js', ui: { pages: [{ id: 'acme.hello_world.home', path: 'x/hello' }], panels: [{ id: 'acme.hello_world.panel' }], commands: [{ id: 'acme.hello_world.wave', label: 'Wave' }], themes: ['acme.hello_world.theme'] } },
  activation_events: [],
};

afterEach(() => {
  deactivatePlugin('acme.hello_world');
  resetActivation();
  resetErrorSink();
  for (const [reg, id] of [[PAGES, 'acme.hello_world.home'], [PANELS, 'acme.hello_world.panel'], [COMMANDS, 'acme.hello_world.wave'], [THEMES, 'acme.hello_world.theme']] as const) {
    const e = (reg as typeof COMMANDS).list().find((x) => x.id === id);
    if (e) (reg as typeof COMMANDS).register(id, e.value as never, { owner: e.owner, replace: true })();
  }
});

it('activates the real hello-world bundle and its contributions appear', async () => {
  const source = readFileSync(BUNDLE, 'utf8');
  const fetchImpl = vi.fn(async (url: string) => new Response(url.endsWith('/api/plugin-factory') ? JSON.stringify({ data: { plugins: [ROW] } }) : source)) as unknown as typeof fetch;
  // Vite refuses to import files outside the project root, so the bundle's
  // (single-export) ESM source is evaluated as a script with `export` rewritten.
  const importImpl = async (): Promise<PluginModule> => {
    const mod: PluginModule = {};
    new Function('module', source.replace(/^export const plugin =/m, 'module.plugin ='))(mod);
    return mod;
  };
  await loadPlugins({ fetchImpl, importImpl, hostVersion: '1.19.0' });
  expect(PAGES.get('acme.hello_world.home')).toBeTruthy(); // the gate
  await act(async () => {
    await fireActivation('onPage:acme.hello_world.home');
  });
  const Gate = PAGES.list().find((e) => e.id === 'acme.hello_world.home')!.value.element as React.ComponentType;
  render(<Gate />);
  expect(await screen.findByText('Hello World')).toBeInTheDocument(); // i18n bundle in plugin:<id> namespace
  expect(THEMES.get('acme.hello_world.theme')?.dark).toBe(true);
  const cmd = COMMANDS.get('acme.hello_world.wave')!;
  await cmd.run();
  expect((globalThis as { __hello_waved?: boolean }).__hello_waved).toBe(true);
  const Panel = PANELS.get('acme.hello_world.panel')!.component as React.ComponentType<{ agentId: string }>;
  render(<Panel agentId="a1" />);
  expect(await screen.findByText('panel for a1')).toBeInTheDocument();
});
