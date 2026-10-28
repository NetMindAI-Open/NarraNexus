/**
 * @file_name: sdk.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: definePlugin validates its shape; the vite preset is a real plugin that resolves/loads the host
 * libraries as synthetic-named-export shims (no external imports left unresolved in the built bundle).
 */
import { describe, expect, it } from 'vitest';

import { HOST_EXTERNALS, definePlugin, hostShimModule, vitePreset } from '../index';

describe('@narranexus/sdk', () => {
  it('definePlugin requires activate', () => {
    expect(() => definePlugin({} as never)).toThrow(/activate/);
    const p = definePlugin({ activate() {} });
    expect(typeof p.activate).toBe('function');
  });

  it('hostShimModule exports the host global as the default (default AND named imports resolve via syntheticNamedExports)', () => {
    expect(hostShimModule('react')).toContain('globalThis.__narranexus_host__.react');
    expect(hostShimModule('lucide-react')).toContain('globalThis.__narranexus_host__.icons');
    expect(() => hostShimModule('lodash')).toThrow(/no host shim/);
  });

  it('vitePreset() is a real Vite/Rollup plugin, not plain config data', () => {
    const plugin = vitePreset({ entry: 'src/main.ts' });
    expect(plugin.name).toBeTruthy();
    expect(typeof plugin.resolveId).toBe('function');
    expect(typeof plugin.load).toBe('function');
  });

  it('resolveId intercepts every host library — and only the host libraries — as a virtual, synthetic-named-export module', () => {
    const plugin = vitePreset();
    const resolveId = plugin.resolveId as (source: string) => { id: string; syntheticNamedExports: boolean } | null;
    for (const specifier of Object.keys(HOST_EXTERNALS)) {
      const resolved = resolveId(specifier);
      expect(resolved).not.toBeNull();
      expect(resolved!.id).toBe(`narranexus-host:${specifier}`);
      // Without this, `import { useNavigate } from 'react-router-dom'` (a named import) would
      // fail to resolve against our shim's single `export default` — Rollup's
      // syntheticNamedExports treats every named import as a property read on the default.
      expect(resolved!.syntheticNamedExports).toBe(true);
    }
    // A plugin's own app-local imports (e.g. './helpers') must NOT be captured by this plugin —
    // only the six declared host libraries are ever intercepted.
    expect(resolveId('./helpers')).toBeNull();
    expect(resolveId('some-other-npm-package')).toBeNull();
  });

  it('load() returns the shim source for a resolved virtual id and defers to other plugins otherwise', () => {
    const plugin = vitePreset();
    const load = plugin.load as (id: string) => string | null;
    expect(load('narranexus-host:react')).toContain('globalThis.__narranexus_host__.react');
    expect(load('narranexus-host:lucide-react')).toContain('globalThis.__narranexus_host__.icons');
    expect(load('/some/real/file.ts')).toBeNull();
  });

  it('produces a single ESM bundle for the plugin build (via the config hook), with no library left external', () => {
    const plugin = vitePreset({ entry: 'src/main.ts', outDir: 'out', fileName: 'bundle.js' });
    const configHook = plugin.config as () => { build: { lib: { entry: string; fileName: () => string }; outDir: string; rollupOptions?: { external?: unknown } } };
    const cfg = configHook();
    expect(cfg.build.lib.entry).toBe('src/main.ts');
    expect(cfg.build.lib.fileName()).toBe('bundle.js');
    expect(cfg.build.outDir).toBe('out');
    // The host libraries are resolved in-bundle now (via resolveId/load above) — they must not
    // also be declared `external`, or Rollup will leave them as literal unresolved imports in
    // the emitted file the way the old design did.
    expect(cfg.build.rollupOptions?.external).toBeUndefined();
  });
});
