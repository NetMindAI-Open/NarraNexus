---
code_file: frontend/src/platform/loader.ts
last_verified: 2026-10-14
stub: false
---

## 2026-10-14 — `bootEverSettled()` (monotonic) next to `pluginsBootSettled()`

`pluginsBootSettled()` is false for every pass in flight (the login-flip pass resets it) — `App.tsx` needs exactly that to hold a
plugin deep link during any pass. A component that only needs "the factory listing has been applied once" (`BrowserNotices`)
would unmount and remount on every later pass if it used that one. `bootEverSettled()` flips to true with the first settle and
never goes back; do not make `pluginsBootSettled()` monotonic. `FactoryBuiltinRow.excluded` is a required boolean: the backend
always sends it and front/back ship together, so a missing field is a bug, not an old payload.

## 2026-10-14 — `excluded` builtin rows drop their UI even when protected

`loadPlugins` calls `disableBuiltinUi` for a builtin row that is `excluded` (left out by the running distribution) as well as
for the old "not enabled and not protected" case. `protected` only means "a user cannot switch this off"; it never meant a
distribution that does not load the plugin still has its UI. `builtin.ui` stays skipped regardless.

Fail-open behavior, by design: when the factory answer cannot be read (non-ok, 15 s timeout, network error) `loadPlugins`
returns `[]`, disables nothing, and the boot still settles — the shell must not hang on a dead backend. A distribution-excluded
builtin's UI then survives until the next successful pass; the per-feature safety net is the feature's own 404
(`BrowserNotices` reads a 404 as "feature absent" and stops). Any other UI that depends on an excluded builtin's routes needs
the same treatment.

## 2026-09-24 ui.toolRenderers（第 18 个注册表）

仅注释：SHELL_REGISTRIES 说明去掉写死的注册表数量（数字每加一个注册表就会说谎）。

## 2026-09-08（复审）— 并发 pass 计数 + 工场请求超时

预挂载 pass 与登录翻转 pass 可能重叠：用 in-flight 计数，归零才 settle，否则先结束的 pass 会让插件页深链接在另一 pass 还在
注册时被送走。`GET /api/plugin-factory` 加 `AbortSignal.timeout(15s)`，后端挂死时 hold 有上界。

## 2026-09-08（本地 E2E 实测）— `pluginsBootSettled()` / `subscribePluginsBoot()`

硬刷新落在插件页 `/app/x/<page>` 时，路由表先于工场应答渲染，`x/hello` 尚未注册就被 `*` 兜底送回 chat——每个插件页
深链接刷新即丢。`loadPlugins()` 现在在开始/结束时翻一个「本轮 boot 已结束」信号（`useSyncExternalStore` 友好），
`App.tsx` 的 `x/*` 保持路由据此决定是等还是走。

## 2026-09-07 — `registerDeclaredUi` 覆盖 `artifactKinds`

manifest 的 `frontend.ui.artifactKinds` → `ARTIFACT_KINDS` 里的门描述符（owner 为插件）+ 激活事件
`onArtifactKind:<id>`；与 renderers/timeline events 同一模式。此前第 17 个注册表只在 `REGISTRIES` 里存在，
插件只能在 `activate(host)` 里命令式注册，用户打开该 kind 的 artifact 时没有任何事件能唤醒插件。

## 2026-09-07 — `SHELL_REGISTRIES` 自动含 `artifactKinds`

禁用内置 UI 行 / 卸载插件时，artifact kind 条目与其它注册表一起按 owner 清除。注释里的表名数 16→17。

## 2026-09-03（批 2d）— 插件加载器：元数据先行，代码按激活

`loadPlugins()`：GET `/api/plugin-factory` → 只取 enabled+loaded+有 frontend 的行 → `registerDeclaredUi` 按
manifest `frontend.ui` 给每个 page/panel/command 在注册表里放一个 **gate**（owner=插件 id）→
`registerActivation` → `fireActivation('onStartup')`。`activatePlugin`：桌面走 `plugin://<id>/…`、web 走工场
assets 路由；manifest 有 `integrity` 就先 sha256（`digestImpl` 可注入——jsdom 的 SubtleCrypto 跨 realm 拒收
ArrayBuffer，测试用 node:crypto）比对，再由 Blob URL `import()`（动态 import 没有 integrity 属性，所以自己校验后
再导入），Blob URL 登记到 errorSink 做归因；`plugin.activate(host)`。任何失败只上报不外抛。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

`loadPlugins` reads `data.builtins` from the factory listing and calls `disableBuiltinUi(id)` for every disabled non-protected builtin, which removes that owner from every shell registry — so a disabled `builtin.teams` has no `/teams/*` pages without any teams-specific code in the loader.

## 2026-09-04 · UI slot points (batch 3d.2)

`registerDeclaredUi` also registers gates for `frontend.ui.conversationKinds` / `messageRenderers` (a renderer gate matching by role / content prefix) / `timelineEvents` (a gate per event type) / `slots` (silent component gates, labelled action gates) and derives `onRenderer:` / `onTimelineEvent:` / `onSlot:` activation events; `disableBuiltinUi` sweeps the new registries too.

## 2026-09-04 · channels as descriptors (batch 4a)

`disableBuiltinUi` also sweeps `ui.channels`.

## 2026-09-07 — command-gate identity, disabledOwners blacklist, illegal-page rejection at registration, assetUrl traversal guard, table-driven SHELL_REGISTRIES (I-1, I-2, C-2, I-5, architecture E3)

The command gate used to detect "has the plugin replaced its own gate yet" by comparing
`real.value.label !== cmd.label` — a plugin that kept the manifest's declared label on its real
command (a very ordinary thing to do) made the gate never hand off, permanently re-running
`fireActivation` on every invocation instead of the real `run()`. Fixed to the same object-identity
check `actionGate.ts` already used: `e.value !== gate`.

`disableBuiltinUi(pluginId)` now also calls `disableOwner(pluginId)` (registry.ts) so a builtin
disabled at boot cannot register again later even via a lazily-loaded contribution point (e.g.
`ui.channels`, `ui.settingsSections`) that runs well after the disable sweep — a one-shot
`removeOwner` at disable time cannot see a registration that has not happened yet.

`registerDeclaredUi` now REJECTS (at registration time, `continue`s past the entry, calls
`reportUiError`) an `/app`-layout page manifest that does not declare `guard: 'protected'`,
instead of letting it reach `PAGES` and only failing later in `pageRoutes.tsx`'s render-time
check. `pageRoutes.tsx`'s own check remains as defense in depth for entries that reach `PAGES`
another way.

`assetUrl` now rejects any manifest asset entry containing a `..` path segment or characters
outside `SAFE_ASSET_ENTRY = /^[A-Za-z0-9._/-]+$/` (I-5's second half — the URL-origin/prefix
hardening is in `host.ts`'s `http.request`).

`SHELL_REGISTRIES` (the array `disableBuiltinUi` iterates to sweep every registry) is now
`Object.values(REGISTRIES)` from `registries/index.ts` instead of a hand-maintained array — four
copies of the 16-registry list used to exist across `host.ts` and `loader.ts`; adding a 17th
registry now only means adding one line to `REGISTRIES` itself.

## 2026-09-07 — plugin pages confined to the `x/` namespace + collision reporting (M-2)

`page.path` used to go into `PAGES` completely unchecked — only `page.id` was deduped
(`if (!PAGES.has(id))`, silently), so two plugins declaring `path: 'reports'` produced two
identical `<Route>`s (react-router v6 keeps the first, the second is dead code with no signal),
and a plugin's static path like `agents/mine` could outrank the builtin dynamic route
`agents/:agentId` under v6's specificity ranking (static beats dynamic) for any agent literally
named "mine". Fixed with three checks, each `reportUiError` + `continue` (never silently
dropped) instead of the old label-free skip:
1. `page.path` must start with `"x/"` — plugin pages live in a namespace builtin routes never
   use, which structurally rules out the static-outranks-dynamic case above.
2. `page.id` collision from a DIFFERENT owner is now reported, not silently ignored — the first
   registrant still wins, but the second registrant's owner learns why its page never appeared
   instead of a silent no-op. The SAME owner re-declaring its own already-registered id is a
   deliberate carve-out (added for M-9): `loadPlugins()` can legitimately run twice for the same
   plugin (once unauthenticated with `[]` rows, again after login re-fetches the real list) — that
   re-run must stay a silent idempotent no-op, not spam a fake "collision" against itself.
3. `page.path` collision across two DIFFERENT ids is now detected by scanning
   `PAGES.list()` for an existing entry with the same `path` — this is the case `id`-dedup alone
   cannot catch.

Interpretive note: the reviewed finding also floated a stricter form (`x/` + the plugin id's last
dot-segment or the full id). That stricter form was NOT implemented — it would reject the
`tests/plugins/hello_world` fixture's own `path: "x/hello"` for plugin id `acme.hello_world`
(last segment `hello_world` ≠ `hello`), which the finding cited as evidence the "x/" convention
already exists. The looser "$x/$ prefix only" rule is the one actually satisfied by that
fixture and is what ships here.

## 2026-09-07 — `loadPlugins()` never disables the "builtin.ui" row

The backend's `GET /api/plugin-factory` `builtins` list carries a manifest-only `builtin.ui` row
(always `enabled: true, protected: true`) representing the shell's own default owner id —
`"builtin.ui"` is what every shell registration gets as `owner` when no explicit owner is passed
(`registry.ts`'s `Registry.register`). This loop now unconditionally skips `b.id === 'builtin.ui'`
before the `!enabled && !protected` check: disabling it would call `disableOwner('builtin.ui')`
(blacklisting the shell's own default owner forever) and `removeOwner('builtin.ui')` on every
shell registry (wiping every builtin page/panel/sidebar/command currently registered under it) —
bricking the whole shell UI. The factory is expected to always report this row as
enabled+protected (which the pre-existing `!enabled && !protected` guard would also have caught),
but this specific id gets an unconditional carve-out rather than trusting that invariant to hold.

## 2026-09-07 — SRI required for copy-installed plugins (architecture E3(a))

`activatePlugin` previously skipped SRI verification entirely whenever `row.frontend.integrity`
was absent (`fetchVerified`'s `if (integrity) {...}` guard is unchanged — this is a NEW check
BEFORE it). `FactoryPluginRow` gained `mode?: 'copy' | 'link'` (mirrors the installer's own
`Mode = Literal["copy", "link"]`, exposed as `"mode": rec.mode` by the backend's plugin listing).
`activatePlugin` now throws `"${id}: missing integrity — ..."` when `row.mode === 'copy'` and
`integrity` is absent — a "copy" install (a downloaded tarball/repo written into the plugin
store) has no other guarantee the bytes that get imported and run with full plugin privileges are
the bytes an admin approved. A "link" install (points at a local dev checkout under active edit)
and any row with no `mode` at all (older backend payloads not yet sending it) are exempt — this
check only fires on POSITIVE evidence of a copy install, it does not fail-closed on absence of
the field. The thrown error is not reported here directly: `activatePlugin` is invoked through
`registerActivation`'s callback, and `activation.ts`'s existing catch already calls
`reportUiError` for any throw from that callback — adding a second `reportUiError` call here
would double-report.
