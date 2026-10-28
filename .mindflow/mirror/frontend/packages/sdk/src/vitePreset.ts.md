---
code_file: frontend/packages/sdk/src/vitePreset.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2e）— 插件 bundle 的构建预设（纯数据，不 import vite）

单文件 ESM、宿主库（react/react-dom/router/zustand/i18next/lucide）external 并映射到
`window.__narranexus_host__.<prop>`（`hostShimModule` 生成运行时垫片）；任何打包器都能消费，且可单元测试。

## 2026-09-07 — a real Vite plugin, not plain build config (C-3)

The 2026-09-03 design marked the six host libraries Rollup `external` and rewrote their import
paths to `narranexus-host:<name>` in the OUTPUT (`rollupOptions.output.paths`) — this produced a
plugin.js containing literal `import ... from "narranexus-host:react"` that no browser can ever
resolve at runtime (there is no import map anywhere, and Vite's `resolveId`/`load` hooks only
run during a build, never for a browser's own module resolution). Every plugin built with the
old preset was permanently broken the moment it left the build step; this was undetected because
`sdk.test.ts` only asserted on the returned config's shape, never on what a real build produces.

`vitePreset()` now returns an actual `Plugin` object (add it to a plugin's own
`vite.config.ts` `plugins: [...]`, it is no longer spreadable build config). Its `resolveId` hook
intercepts the six bare specifiers and resolves them to a virtual `narranexus-host:<name>` id
with `syntheticNamedExports: true`; its `load` hook returns `export default
globalThis.__narranexus_host__.<prop>` for that id. Rollup bakes this source directly into the
bundle like any other module — the six libraries are never external and the specifier never
appears in the emitted file. `syntheticNamedExports: true` is why both `import Icon from
'lucide-react'` and `import { Home } from 'lucide-react'`-style plugin code resolve correctly
without this file knowing lucide-react's full named-export list: Rollup treats any named import
from a `syntheticNamedExports` module as a property read on its default export.

`VitePresetResult` is gone (the return type is `vite`'s `Plugin`); `hostShimModule` still throws
on an unknown specifier. `tests/plugins/hello_world/frontend/dist/plugin.js` (repo-root `tests/`,
outside `frontend/`) still ships a hand-written bundle built against the OLD preset shape and was
not regenerated as part of this fix — it is out of this file's edit scope.
