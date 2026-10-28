/**
 * @file_name: vitePreset.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The Vite/Rollup plugin a plugin author adds to their own `vite.config.ts` to build a single,
 * self-contained ESM plugin bundle: the six host libraries are baked in as reads off
 * `globalThis.__narranexus_host__` at build time, not left as unresolved external imports.
 *
 * 2026-09-07 (C-3): `vitePreset()` used to return plain build config with the six libraries
 * marked Rollup `external` and their import paths rewritten to `narranexus-host:<name>` in the
 * OUTPUT via `output.paths`. That produced a plugin.js containing literal
 * `import ... from "narranexus-host:react"` statements — a specifier no browser can ever
 * resolve at runtime (there is no import map anywhere for it, and Vite's `resolveId`/`load`
 * hooks only run during a build, never for a browser's own module resolution). Every plugin
 * bundle built with the old preset was permanently broken the moment it left the build step.
 * Fixed by intercepting the six bare specifiers via `resolveId`/`load` INSIDE THE BUILD instead:
 * they resolve to a virtual `narranexus-host:<name>` module id, whose `load()` source is
 * `export default globalThis.__narranexus_host__.<prop>`, and Rollup inlines that source into
 * the bundle like any other module. The specifiers never appear in the emitted file at all —
 * the built plugin.js reads the host global directly.
 */
import type { Plugin } from 'vite';

export const HOST_EXTERNALS: Record<string, string> = {
  react: 'react',
  'react-dom': 'reactDom',
  'react-router-dom': 'router',
  zustand: 'zustand',
  i18next: 'i18next',
  'lucide-react': 'icons',
};

const VIRTUAL_PREFIX = 'narranexus-host:';

/**
 * Source for the virtual module a host library specifier resolves to. Only `export default` is
 * declared — `react-router-dom` and `lucide-react` are consumed almost entirely via named
 * imports (`useNavigate`, individual icons), and there is no way to statically enumerate every
 * name either library exports. Declaring the resolved id with `syntheticNamedExports: true` (see
 * `vitePreset`) tells Rollup to treat any named import from it as a property read on this
 * default export instead, so both `import Icon from 'lucide-react'`-style and
 * `import { Home } from 'lucide-react'`-style plugin code resolve correctly without this file
 * knowing the export list.
 */
export function hostShimModule(specifier: string): string {
  const prop = HOST_EXTERNALS[specifier];
  if (!prop) throw new Error(`no host shim for ${specifier}`);
  return `export default globalThis.__narranexus_host__.${prop};`;
}

export interface VitePresetOptions {
  entry?: string;
  outDir?: string;
  fileName?: string;
}

/**
 * `vitePreset()` returns a real Vite plugin — add it to a plugin's own `vite.config.ts`
 * `plugins: [vitePreset()]`, it is not build config to spread into `defineConfig()` directly.
 * The `config()` hook it implements supplies the lib-mode build settings (single ESM file,
 * `inlineDynamicImports`); the `resolveId`/`load` hooks supply the host-library shims.
 */
export function vitePreset(options: VitePresetOptions = {}): Plugin {
  const fileName = options.fileName ?? 'plugin.js';
  return {
    name: 'narranexus-host-shim',
    resolveId(source) {
      if (!(source in HOST_EXTERNALS)) return null;
      return { id: `${VIRTUAL_PREFIX}${source}`, syntheticNamedExports: true };
    },
    load(id) {
      if (!id.startsWith(VIRTUAL_PREFIX)) return null;
      return hostShimModule(id.slice(VIRTUAL_PREFIX.length));
    },
    config() {
      return {
        build: {
          lib: { entry: options.entry ?? 'src/index.ts', formats: ['es'], fileName: () => fileName },
          outDir: options.outDir ?? 'dist',
          emptyOutDir: true,
          rollupOptions: { output: { inlineDynamicImports: true } },
          sourcemap: false,
          minify: true,
        },
        define: { 'process.env.NODE_ENV': '"production"' },
      };
    },
  };
}
