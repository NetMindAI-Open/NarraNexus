/**
 * Dependency-direction gate for the plugin platform.
 *
 * 2026-09-07 (M-7): the original two rules below named a plugin host that never landed at the
 * paths this file expected — `src/plugins/` and `src/contracts/` were never created; the actual
 * plugin host (host.ts, loader.ts, gates.tsx, the activation/error-sink/i18n/page-route plumbing)
 * landed under `src/platform/` instead. Both rules matched zero files (`from: { path:
 * '^src/plugins/' }` / `'^src/contracts/'`) and had been silently inert since batch 0 — green by
 * vacuous truth, not by enforcement. Replaced with rules against the paths that actually exist.
 */
module.exports = {
  forbidden: [
    {
      name: 'platform-host-is-app-agnostic',
      comment:
        'The host/loader kernel (host.ts, loader.ts, gates.tsx, activation.ts, actionGate.ts, ' +
        'PluginBoundary.tsx, SlotOutlet.tsx, pageRoutes.tsx, PluginStatus.tsx, errorSink.ts, ' +
        'i18n.ts, bootPlugins.ts) must not import app UI (src/components/**, src/pages/**) — a ' +
        'plugin host that reaches into the app it hosts cannot be reused across distributions. ' +
        'It may depend on itself, src/stores/** and src/lib/** (session/base-URL/platform ' +
        'utilities, not UI). src/platform/builtin.ts and builtinPanels.tsx are NOT covered here: ' +
        'they are the shell\'s own wiring that deliberately registers app components into the ' +
        'host\'s registries, the one place that direction is expected to invert.',
      severity: 'error',
      from: {
        path: '^src/platform/(host|loader|gates|activation|actionGate|PluginBoundary|SlotOutlet|pageRoutes|PluginStatus|errorSink|i18n|bootPlugins|whenContext)\\.tsx?$',
      },
      to: { path: '^src/(components|pages)/' },
    },
    {
      name: 'registries-are-pure',
      comment:
        'src/platform/registries/** holds types and registries only — it imports nothing else from src/ ' +
        'except src/types/** by type-only reference (a registry value type names a wire type); a VALUE ' +
        'import from src/types is still forbidden.',
      severity: 'error',
      from: { path: '^src/platform/registries/' },
      to: { path: '^src/', pathNot: '^src/(platform/registries|types)/' },
    },
    {
      name: 'registries-take-only-types-from-src-types',
      comment: 'the src/types exception above is for type-only imports; runtime code (src/types/artifact.ts exports a function) must not reach the registries',
      severity: 'error',
      from: { path: '^src/platform/registries/' },
      to: { path: '^src/types/', dependencyTypesNot: ['type-only'] },
    },
    {
      name: 'no-circular',
      severity: 'warn',
      from: {},
      to: { circular: true },
    },
  ],
  options: {
    doNotFollow: { path: 'node_modules' },
    tsConfig: { fileName: 'tsconfig.app.json' },
    tsPreCompilationDeps: true,
    exclude: { path: '__tests__|\\.test\\.|test-setup' },
  },
};
