---
code_file: frontend/src/components/awareness/FileUpload.tsx
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 原生 alert 换成应用内通知

wry（Tauri webview）**不渲染** `window.alert`，调用直接返回、什么都不发生。所以桌面端
workspace 文件下载失败无提示。注意 `TreeNode` 自己拿一个 `useConfirm()` 实例 —— 下载按钮和它的 URL 都在这个子组件里，把回调穿过递归换不到任何好处（空闲的 dialog 只是一个 useState + null 渲染）。改用 [[ConfirmDialog]] 的 `useNotice()`，与仓库既有的 20+ 处 confirm 先例同一条路。

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

## 2026-07-30 — the file preview needs code width

Owner, same report as [[AwarenessPanel]]'s edit modal: "workspace 里文件查看也
会弹出,太窄了". `size="lg"` (512px) for a viewer showing source, logs and data.

`size="5xl"` (1024px), body `max-h-[78vh]`, images `max-h-[68vh]`. The `<pre>`
soft-wraps (`whitespace-pre-wrap`), so at 512px nearly every line wrapped, and
each continuation loses the leading indentation that makes structured text
legible. Kept soft-wrap rather than switching to horizontal scroll: that trade
belongs to a separate decision, and forcing sideways scrolling on log files is
its own annoyance.

Wider than the awareness editor (4xl) deliberately — code benefits from columns
that prose does not. The sibling `RegisterModal` stays `md`: it IS a form.

## 2026-06-16 — workspace Download button now calls downloadFile()

The per-file Download control in `TreeNode` was previously an
`<a href download>` against `api.workspaceFileRawUrl()`. This silently
failed on both the DMG (WKWebView mixed-content block) and `bash run.sh`
(cross-origin, `download` attribute ignored; workspace endpoints also
require `X-User-Id` / `Authorization` headers an `<a>` cannot carry).

The control is now a `<button>` that calls
`downloadFile({ url: downloadUrl, filename: node.name, authHeaders: api.getAuthHeaders() })`
from `lib/download.ts`. Auth headers are required here (unlike artifact
downloads) because workspace file raw endpoints are auth-gated.

## 2026-05-27 — sub-folders default to expanded (P0 fix)

`TreeNode`'s default-expand was `depth < 1`, so only top-level folders
opened on render; sub-folders showed their name but no contents,
easily misread as "sub-folder is ignored". P0 bug 2026-05-18 (Xinyao
Hu). Backend returns the full recursive tree, so showing it all at
once matches the user mental model — they can still collapse with the
chevron. Default is now `true` regardless of depth.

`TreeNode` is now a named export (in addition to the default
`FileUpload`) so tests can render it directly without spinning up the
zustand stores and api wrapper. Test pin in
`__tests__/FileUpload.test.tsx`.

## 2026-05-15 — fix inner-scroll discoverability

Tree's inner `<ScrollArea>` now uses `type="auto"` (always-visible scrollbar when overflow exists) and `max-h-[55vh]` instead of the original `max-h-[260px]` hover-only setup. The previous combination — hidden scrollbar + small cap + outer AwarenessPanel ScrollArea swallowing chained wheel events — made users think the tree couldn't scroll. Paired with `overscroll-contain` becoming a default in `ui/scroll-area.tsx` the wheel now stays inside the tree viewport until its boundary.

## 2026-05-14 — workspace tree viewer + manual register

The flat file list became a **collapsible directory tree** (the backend
already filters dotfolders, so the UI doesn't need to). Per-file actions:

- **Download** — `<a href download>` against `api.workspaceFileRawUrl`.
- **Preview** — opens a `Dialog` and fetches the file via
  `api.fetchWorkspaceFileBlob`. Text files (md/csv/json/txt/html/code) are
  shown in a `<pre>`; images in `<img>`; everything else surfaces a
  "preview unavailable, download instead" message. Text preview is capped
  at 200 KB to keep huge files from freezing the modal.
- **Register as artifact** — opens a `Dialog` with `kind` (auto-detected
  from extension, editable) + `title` (default = filename without ext),
  then calls `artifactsApi.registerFromWorkspace`. Same runner the MCP
  tool uses, so validation is identical (path must live in a workspace
  subdirectory, kind whitelist, quota).
- **Delete** — works on both files and directories (recursive); confirms
  via `useConfirm` before issuing the DELETE.

Top-level drag-and-drop / file-picker upload is unchanged.

# FileUpload.tsx — Workspace tree viewer (config panel)

Hosts the workspace section of the agent config drawer. Lets the user
browse, preview, download, register-as-artifact, delete files and folders
in the agent's workspace, and drag-drop new files into the root.

Used inside `AwarenessPanel`. Owns its own local tree state (no
`usePreloadStore`).

## 2026-07-13 — office 文件走 register-as-artifact

注册弹窗新增 kind 选项 `application/vnd.officecli-live`('Office document (live)'),`detectKindFromExt` 把 .pptx/.docx/.xlsx 映射到它。office 文件用**现有的 register 按钮**即可(不再有独立的'实时预览'按钮——早先短暂加过又移除,合并进 register)。`onRegistered` 现在会 `loadPinned` 刷新面板,注册后 tab 立刻出现(此前是 no-op,得手动刷新)。
