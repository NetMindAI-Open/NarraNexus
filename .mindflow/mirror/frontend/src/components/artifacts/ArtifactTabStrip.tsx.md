---
code_file: frontend/src/components/artifacts/ArtifactTabStrip.tsx
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 原生 alert 换成应用内通知

wry（Tauri webview）**不渲染** `window.alert`，调用直接返回、什么都不发生。所以桌面端
删除制品失败时只有「确认弹窗没关」这一个弱信号，没有原因。改用 [[ConfirmDialog]] 的 `useNotice()`，与仓库既有的 20+ 处 confirm 先例同一条路。

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

## 2026-07-30 — width budget: actions on demand, "+" pinned outside the scroller

Owner report: the artifacts page "数据不全". Cause was a width budget, not
missing data. Every tab rendered its three action buttons (zoom / minimize /
delete) unconditionally, putting a ~200px floor under each tab. The artifact
column's minimum is 320px — its normal size whenever the chat keeps most of
the room — so ONE tab filled the strip and pushed the "+" new-tab entry past
the right edge, reachable only by horizontally scrolling a row whose scrollbar
isn't visible. Effectively: the controls existed but couldn't be found.

- Actions now occupy space only on the **active** tab, or while a tab is
  hovered / holds focus. Inactive tabs collapse to their truncated title.
- They collapse via `w-0 opacity-0`, deliberately **not** `hidden`: a
  `display: none` subtree can't take focus, so `group-focus-within` would
  never fire and keyboard users would lose zoom / minimize / delete outright.
- The **"+" moved out of the scrolling row** into a sibling that can't scroll
  away, so the new-tab entry is always on screen.

## 2026-07-22 — trailing "+" new-tab entry

Added an always-present trailing `+` button (even with zero artifacts) opening
[[NewTabOmnibox.tsx]] — the browser-style new-tab affordance for opening a URL
or picking an existing artifact.
## 2026-05-14-r3 — delete popup simplified to a single confirm + notice

The two-state checkbox dialog (delete tab only / delete tab + files) is
gone. Deletion is registry-only end-to-end (see the agents_artifacts
backend mirror md for the rationale). The dialog now just confirms the
tab removal and tells the user where to clean up workspace files if they
want to — "use the workspace section of the config panel".

## 2026-05-14 — delete-source popup (pointer model)

The 🗑️ button no longer fires `window.confirm` + immediate delete. It opens
an inline `Dialog` with a checkbox:

> [ ] Also delete the workspace source files

Off (default) → `delete(agentId, artifactId, false)` — drop the DB row,
keep the agent's working files. On → `delete(agentId, artifactId, true)` —
also rmtree the artifact root in the agent's workspace. The confirm
button label flips to make the choice obvious ("Delete tab only" vs
"Delete tab + files").

## 2026-05-14 — Zoom affordance

New required prop `onZoom(artifactId)` (provided by `[[ArtifactColumn]]`).
Surfaces two ways:

- `Maximize2` icon button on each tab, between title and the minimize
  button.
- Double-click on the tab body — `onDoubleClick` calls `onZoom`. Tooltip
  on the tab body advertises both interactions.

Single click still calls `setActive` (preserves the original tab-as-
selector UX); zoom is strictly opt-in.

# ArtifactTabStrip.tsx — Horizontal tab bar for artifact navigation

## Why it exists

`ArtifactColumn` needs a compact multi-tab navigation control that shows all open artifacts for the current agent session, allows the user to switch between them, and exposes pin and close actions per tab. The strip is the sole navigation surface for the artifact column — there is no sidebar list or dropdown alternative.

## Upstream / Downstream

- **Rendered by**: `ArtifactColumn` at the top of the content area.
- **Reads from**: `artifactStore` — `artifacts[]`, `activeArtifactId`, `setActive`, `pin`, `delete`.
- **Writes to**: `artifactStore` via `setActive` (tab click), `pin` (pin button), `delete` (close button).

## Pin emoji semantics

The pin/unpin affordance uses two emoji characters chosen for their visual metaphor:
- `📌` (pinned): the pushpin appears embedded — "this item is stuck in place." Shown when `artifact.pinned === true`.
- `📍` (round pushpin): looks like it is about to be planted — "you can pin this." Shown when `artifact.pinned === false`.

This avoids needing a separate icon library import for a minor control. The emoji render consistently at 12px in all major browsers.

## Event propagation

Each tab `<div>` has an `onClick` that calls `setActive`. The pin and close `<button>` elements inside each tab call `e.stopPropagation()` so their actions do not also trigger tab activation. Without stop-propagation, clicking "delete" would activate the tab one frame before it is removed — causing a flash and a stale `activeArtifactId` in the store.

## Design decisions

**`overflow-x-auto` on the strip container**: When many artifacts are open, the strip overflows horizontally rather than wrapping. This preserves the height of the strip at exactly one tab row, which keeps the ArtifactColumn header a fixed height that the layout can rely on.

**No virtualization**: The typical agent session produces a handful of artifacts. Full virtualization of the tab strip would add complexity with no measurable benefit. If sessions with 50+ artifacts become common, a dropdown overflow menu would be the right upgrade, not virtualization.

**`border-b border-[var(--border-default)]`** on the strip container — matches the visual weight of other column header separators in the app. The border sits between the tab strip and the renderer area.

## Gotchas

`ArtifactTabStrip` renders an "empty" message (`No artifacts yet`) when `artifacts.length === 0`. In practice, `ArtifactColumn` returns `null` before rendering the strip when there are no artifacts, so this empty state is a safety fallback that should never be visible to users. If the visibility logic in `ArtifactColumn` changes, this fallback becomes important.
