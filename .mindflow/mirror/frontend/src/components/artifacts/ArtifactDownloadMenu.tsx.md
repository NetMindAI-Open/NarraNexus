---
code_file: frontend/src/components/artifacts/ArtifactDownloadMenu.tsx
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 改查 `ARTIFACT_KINDS` 注册表并订阅

`KIND_REGISTRY[kind]` → `ARTIFACT_KINDS.get(kind)`，并 `useRegistryEntries(ARTIFACT_KINDS)` 订阅：插件在首屏之后
注册的 kind 会让已打开的视图重渲染，而不是停在「不支持的类型」。

## 2026-08-19 — 关闭语义并入 useDismissOnOutside

本地 `mousedown`+`keydown` 关闭段删除,交给
[[../../hooks/useDismissOnOutside]](trigger/menu 两个 ref 走 extraRefs)。
与全仓弹层同一时机:capture pointerdown(pen/touch 同样命中,中途
stopPropagation 拦不住)。原 effect 只剩 scroll/resize 的 recompute
(capture window scroll 保留——内层 overflow 容器滚动时面板要跟位)。

## 2026-08-12 — 注册表数组化后取「最后挂载」的实例

`chartInstances` 由单槽 `Record<id, T|null>` 改为数组 `Record<id, T[]>`
(权威定义见 [[artifactStore]]),导出取 `chartInstances[id]?.at(-1)`。
**为什么是 `.at(-1)` 不是 `[0]`**:列 pane 与 zoom modal 可同时挂同一
artifact,modal 后挂载即屏幕上那个;取 `[0]` 会在 modal 打开时导出背后
列里那个实例。**为什么不能判 `if (!instance)` 后直接用**:数组语义下空
数组也为真——判空要判 `.at(-1)` 的结果(undefined),不是判数组本身。

## 2026-07-30 — 原生 alert 换成应用内通知

wry（Tauri webview）**不渲染** `window.alert`，调用直接返回、什么都不发生。所以桌面端
「图表还没加载完」这条提示彻底消失；而**原文件下载压根没有错误处理** —— 浏览器里是未处理的 promise rejection（静默），桌面端是不可见 alert，两个平台都没有可用的错误路径。现在成功时还会announce `savedPath`（桌面端没有下载条，不说用户就不知道文件去哪了）。改用 [[ConfirmDialog]] 的 `useNotice()`，与仓库既有的 20+ 处 confirm 先例同一条路。

**chrome 不在调用点重复**：标题 / OK 文案 / danger 由 `useNotice` 提供，调用点只写
message。第一版把这三行在 6 个文件里复制了 9 遍（评审点名），改文案要改 9 处。这同时把
`useConfirm` 默认值 `'Notice'` / `'OK'` 硬编码英文、不走 i18n 的洞补在一处 ——
不必去动那个 20+ 调用方共用的原语。共享 key
`common.{noticeTitle,doneTitle,actionFailedTitle,ok}`，10 语言。

`notifyDone` 与 `notifyPending` 是分开的：`noticeTitle` 的 10 个译法都是「请稍候」语义
（稍等一下 / 少しお待ちください / Одну секунду），拿它当成功提示的标题会让用户以为还在
进行中 —— 所以成功走 `doneTitle`。

用一条**仓库级静态契约测试**钉住（`lib/__tests__/no-native-dialogs.test.ts`）：扫描全部
源文件，禁止任何 `window.alert/confirm/prompt` 调用。这类 bug 前两轮都是靠人读代码发现的
—— 单元测试反而 stub 掉了 `window.confirm` 因而什么都没证明。grep 是唯一能覆盖「还没被
写出来的文件」的断言。

# ArtifactDownloadMenu.tsx — Per-artifact download / export dropdown

## Why it exists

The small download/export affordance in the artifact column header (and in
`[[ArtifactZoomModal]]`). For chart artifacts it offers PNG/JPEG export (via the
last live ECharts instance in `[[artifactStore]].chartInstances`, a per-id
list) plus the raw JSON; for everything else, just "Download original" against
the token-protected raw URL minted by `useArtifactRawUrl`.

## 上下游关系
- **被谁用**: `[[ArtifactColumn]]` (header toolbar), `[[ArtifactZoomModal]]` (header).
- **依赖谁**: `useArtifactStore` (chart instances), `useArtifactRawUrl` (signed URL).

## 设计决策

**Portal-mounted panel (2026-05-20 rewrite)**: the dropdown is rendered through
`createPortal(..., document.body)` and positioned with `fixed` coordinates
derived from the trigger button's `getBoundingClientRect()` (right-aligned to
the trigger's right edge, 4px below it). This is **not optional polish** — every
ancestor of the artifact column (`MainLayout` `<main>`/group, `ArtifactColumn`
`<aside>` and its content div) sets `overflow-hidden` for flex-sizing
correctness. The previous implementation used a native `<details>/<summary>`
with an `absolute right-0 top-full` child, which got clipped by that
overflow chain down to a tiny sliver — the "small weird window" bug. Portal is
the same escape hatch `[[ArtifactZoomModal]]` and `ui/Dialog` use. `z-index`
alone does **not** fix overflow clipping — they're independent.

**Controlled open state**: `useState(open)` + click-outside (`mousedown` on
document, ignoring clicks inside trigger/menu) + Escape to close, replacing the
uncontrolled `<details>` toggle. Position recomputes on `scroll` (capture phase,
so inner overflow containers count) and `resize` while open.

## 2026-06-16 — "Download original" now calls downloadFile()

The "Download original" entry previously used a raw `<a href download>`
against the token-protected URL returned by `useArtifactRawUrl`. This
silently failed on both the DMG (WKWebView mixed-content block) and
`bash run.sh` (cross-origin, `download` attribute ignored). It now
calls `downloadFile({ url, filename })` from `lib/download.ts`, which
picks the correct strategy per runtime surface. Artifact raw URLs are
public (access token in query string), so `authHeaders` is omitted.

Chart PNG/JPEG export (using a `data:` URL from the live ECharts
canvas instance) is unchanged — that path never hits a backend endpoint
and does not suffer from cross-origin or mixed-content issues.

## Gotcha / 边界情况

- Chart export reads `chartInstances[artifact_id]?.at(-1)` lazily via
  `useArtifactStore.getState()` at click time; if no instance is mounted yet
  (`.at(-1)` is undefined) it notifies "still loading" rather than failing
  silently. The value is a LIST — never truthiness-test the array itself
  (an empty array is truthy); test the `.at(-1)` result.
- `right` is computed as `window.innerWidth - rect.right`; if the trigger ever
  sits near the right viewport edge with a >200px menu, the menu stays pinned to
  the trigger's right edge (acceptable — the menu has room to the left).

## 2026-08-21 — ext 改走 `downloadExtFor(artifact)`(深圳复测 .bin bug)

office-live kind 静态查表拿不到扩展名(pptx/docx/xlsx 共用一个 kind),
`?? 'bin'` 兜底让 office 产物一律下成 .bin。改为
[[kindRegistry.ts]]`.downloadExtFor`:静态 ext 优先,缺则取
`artifact.file_path` 的扩展名(净化后),最后才 'bin'。下载文件名由
`a.download`/Tauri 落盘决定,Content-Type 不参与——所以这里是该 bug
的**决定性一层**(后端 raw 路由的 MIME 是次要面,见 [[raw_access.py]])。

## 2026-08-19 — KIND_TO_EXT/isChart 改查注册表

私有 ext 映射删除,`downloadExt` 与 `chartImageExport` 从
[[kindRegistry.ts]] 查;'bin' 兜底语义不变。
