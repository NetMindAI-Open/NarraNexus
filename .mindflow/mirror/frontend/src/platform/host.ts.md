---
code_file: frontend/src/platform/host.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — `registries.artifactKinds`

无代码改动（门面按 `REGISTRIES` 派生），注释里的注册表数 16→17。插件通过它注册自己的 artifact 渲染器；
不能覆盖壳的内置 kind。

## 2026-09-03（批 2d）— `HostAPI`：插件 `activate(host)` 拿到的全部宿主面

刻意最小（Grafana Angular 教训）：共享框架库（react/react-dom/router/zustand/i18next/lucide，插件 bundle 把它们
标 external，用宿主单份）、六个注册表、`register()`（以插件 id 注册、撤销挂到 `subscriptions`；只允许替换
自己 owner 的条目——即加载器替它登记的 gate）、`http.request`（只收相对路径、带会话鉴权头）、
`http.prefix=/api/x/<id>`、`i18n`（自动 `plugin:<id>` 命名空间）、`log.error`（归因到本插件）、`dispose()`
逆序展开。`exposeHostGlobals()` 把框架库挂到 `window.__narranexus_host__` 一次，供 SDK vite preset 的
externals 解析。stores/组件/内部实现一律不承诺。

## 2026-09-04 · UI slot points (batch 3d.2)

`HostAPI.registries` gains `messageRenderers`, `timelineEvents`, `conversationKinds` and the six slot points (`chatHeaderActions`, `composerExtensions`, `messageActions`, `sidebarSections`, `agentCardBadges`, `topBarItems`); `host.register` works on them like on the structural registries (disposer tracked, may replace only its own gate).

## 2026-09-04 · channels as descriptors (batch 4a)

`registries.channels` — a channel plugin registers its Channels-section row.

## 2026-09-07 — per-registry facade (`PluginRegistryHandle<T>`), URL-origin hardening, HOST_API_VERSION cross-check (I-4, I-5, architecture E3/E4)

`host.registries.*` used to hand a plugin the module-level `Registry` instance directly
(`host.registries.pages === PAGES`), so nothing actually stopped a plugin from calling
`removeOwner()` / `freeze()`, or registering under another plugin's owner, other than the
`host.register()` convention nobody was required to use. Each of the 16 entries is now a
`PluginRegistryHandle<T>` — `register`/`replace`/`dispose`/`list` only — built by
`makeRegistryHandle(registry, pluginId, subscriptions)`, which fixes `owner` to `pluginId` via
closure. `PluginRegistries`'s type is `{ [K in keyof typeof REGISTRIES]: PluginRegistryHandle<...> }`
computed from `registries/index.ts`'s `REGISTRIES` table, so a 17th registry added there needs no
changes here. `host.register(handle, id, value)` is kept ONLY as a thin back-compat alias
delegating to `handle.register(id, value)` — `tests/plugins/hello_world/frontend/dist/plugin.js`
(repo-root `tests/`, outside this repo's `frontend/`/`tauri/` slice) still calls the old 3-arg
form and could not be updated as part of this change.

`http.request` (I-5) now resolves the requested path against the base URL with `new URL(path,
base)` and compares `url.origin !== baseOrigin` — the old check only tested that the path started
with `/`, which a protocol-relative `//evil.example/collect` also satisfies (browsers resolve a
leading `//` to a different origin). It additionally restricts to the plugin's own
`/api/x/<id>` prefix (`url.pathname !== prefix && !startsWith(prefix + '/')` throws), closing the
gap where any plugin could call any OTHER plugin's route.

`HOST_API_VERSION` (E4) is the frontend half of the "ui" kind's contract version
(`packages/narranexus-contracts/src/narranexus/contracts/__init__.py`'s `API_VERSIONS["ui"]`); a
cross-check test in `host.test.tsx` asserts the two numbers match (both `1` as of 2026-09-07 —
briefly drifted to frontend `1` / Python `0`, caught by this test before it shipped).
