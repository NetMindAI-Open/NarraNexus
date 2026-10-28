/**
 * @file_name: host.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: HostAPI registers as the plugin, tracks disposals, unwinds on dispose, exposes the framework globals once, and restricts http.request to the plugin's own same-origin prefix.
 */
import * as React from 'react';
import * as ReactDOM from 'react-dom';
import * as ReactRouter from 'react-router-dom';
import * as Zustand from 'zustand';
import i18next from 'i18next';
import * as Icons from 'lucide-react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HOST_API_VERSION, createHostApi, exposeHostGlobals } from '@/platform/host';
import { COMMANDS } from '@/platform/registries';

afterEach(() => {
  for (const e of COMMANDS.list().filter((x) => x.owner === 'acme.t')) COMMANDS.register(e.id, e.value, { owner: e.owner, replace: true })();
});

describe('HostAPI', () => {
  it('registers under the plugin id and disposes everything on dispose()', () => {
    const host = createHostApi('acme.t', '1.19.0');
    host.registries.commands.register('acme.t.cmd', { label: 'x', run: () => undefined });
    expect(COMMANDS.list().find((e) => e.id === 'acme.t.cmd')?.owner).toBe('acme.t');
    expect(host.subscriptions).toHaveLength(1);
    host.dispose();
    expect(COMMANDS.has('acme.t.cmd')).toBe(false);
    expect(host.subscriptions).toHaveLength(0);
  });

  it('the back-compat host.register(handle, id, value) alias delegates to handle.register', () => {
    const host = createHostApi('acme.t', '1');
    host.register(host.registries.commands, 'acme.t.cmd', { label: 'x', run: () => undefined });
    expect(COMMANDS.get('acme.t.cmd')?.label).toBe('x');
  });

  it('may replace its own gate entry but not another owner\'s entry', () => {
    COMMANDS.register('acme.t.cmd', { label: 'gate', run: () => undefined }, { owner: 'acme.t' });
    const host = createHostApi('acme.t', '1');
    host.registries.commands.register('acme.t.cmd', { label: 'real', run: () => undefined });
    expect(COMMANDS.get('acme.t.cmd')?.label).toBe('real');
    COMMANDS.register('other.cmd', { label: 'o', run: () => undefined }, { owner: 'other' });
    expect(() => host.registries.commands.register('other.cmd', { label: 'steal', run: () => undefined })).toThrow(/already registered/);
    expect(() => host.registries.commands.replace('other.cmd', { label: 'steal', run: () => undefined })).toThrow(/already registered/);
    COMMANDS.register('other.cmd', { label: 'o', run: () => undefined }, { owner: 'other', replace: true })();
  });

  it('dispose(id) removes exactly that one entry, not every entry the plugin owns', () => {
    const host = createHostApi('acme.t', '1');
    host.registries.commands.register('acme.t.a', { label: 'a', run: () => undefined });
    host.registries.commands.register('acme.t.b', { label: 'b', run: () => undefined });
    host.registries.commands.dispose('acme.t.a');
    expect(COMMANDS.has('acme.t.a')).toBe(false);
    expect(COMMANDS.has('acme.t.b')).toBe(true);
    expect(host.subscriptions).toHaveLength(1); // only b's disposer remains tracked
    host.dispose();
  });

  it('list() shows every entry in the registry, not just this plugin\'s own', () => {
    COMMANDS.register('other.cmd2', { label: 'o', run: () => undefined }, { owner: 'other' });
    const host = createHostApi('acme.t', '1');
    host.registries.commands.register('acme.t.cmd', { label: 'x', run: () => undefined });
    const ids = host.registries.commands.list().map((e) => e.id);
    expect(ids).toContain('acme.t.cmd');
    expect(ids).toContain('other.cmd2');
    host.dispose();
    COMMANDS.register('other.cmd2', { label: 'o', run: () => undefined }, { owner: 'other', replace: true })();
  });

  it('exposes exactly the host copies of the six shared libraries (not merely "defined")', () => {
    exposeHostGlobals();
    const globals = window.__narranexus_host__;
    expect(globals?.react).toBe(React);
    expect(globals?.reactDom).toBe(ReactDOM);
    expect(globals?.router).toBe(ReactRouter);
    expect(globals?.zustand).toBe(Zustand);
    expect(globals?.i18next).toBe(i18next);
    expect(globals?.icons).toBe(Icons);
  });

  it('exposeHostGlobals is idempotent: a second call keeps the same object', () => {
    exposeHostGlobals();
    const first = window.__narranexus_host__;
    exposeHostGlobals();
    expect(window.__narranexus_host__).toBe(first);
  });

  it('http.request refuses an absolute cross-origin URL', async () => {
    const host = createHostApi('acme.t', '1');
    await expect(host.http.request('https://evil')).rejects.toThrow(/relative/);
  });

  it('http.request refuses a protocol-relative URL (the `//host/...` origin-check bypass)', async () => {
    const host = createHostApi('acme.t', '1');
    // `//evil.example/collect` starts with "/" (the old, insufficient check) but browsers
    // resolve a leading "//" as protocol-relative, landing on a different origin.
    await expect(host.http.request('//evil.example/collect')).rejects.toThrow(/relative/);
  });

  it('http.request refuses a same-origin path outside the plugin\'s own /api/x/<id> prefix', async () => {
    const host = createHostApi('acme.t', '1');
    await expect(host.http.request('/api/x/other-plugin/anything')).rejects.toThrow(/must start with/);
    await expect(host.http.request('/api/jobs')).rejects.toThrow(/must start with/);
  });

  it('http.request allows the plugin\'s own prefix', async () => {
    const host = createHostApi('acme.t', '1');
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }));
    await host.http.request('/api/x/acme.t/widgets');
    expect(fetchSpy).toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  // Architecture E4: HOST_API_VERSION is the frontend half of the "ui" kind's contract version
  // (packages/narranexus-contracts/src/narranexus/contracts/__init__.py `API_VERSIONS["ui"]`) —
  // a manifest declares `api.ui`, and the Python-side loader enforces
  // `MIN_SUPPORTED_VERSIONS["ui"] <= api.ui <= API_VERSIONS["ui"]`. This test cross-checks the
  // two numbers so they cannot silently drift apart again (2026-09-07: they did, briefly — see
  // git history for the `it.fails` this test used to be while the Python side caught up).
  it('HOST_API_VERSION matches API_VERSIONS["ui"] in the Python contracts module', async () => {
    const { readFile } = await import('node:fs/promises');
    const { resolve } = await import('node:path');
    // `process.cwd()` (not `import.meta.url`) — vitest's jsdom environment does not guarantee
    // `import.meta.url` is a resolvable `file://` URL, but the test runner's cwd is reliably
    // `frontend/` (this repo's vitest root), two levels above the sibling `packages/` directory.
    const contractsPath = resolve(process.cwd(), '../packages/narranexus-contracts/src/narranexus/contracts/__init__.py');
    const source = await readFile(contractsPath, 'utf8');
    const match = /"ui":\s*(\d+)/.exec(source);
    expect(match).not.toBeNull();
    const pythonUiVersion = Number(match![1]);
    expect(HOST_API_VERSION).toBe(pythonUiVersion);
  });
});
