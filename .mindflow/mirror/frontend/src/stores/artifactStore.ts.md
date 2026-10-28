---
code_file: frontend/src/stores/artifactStore.ts
last_verified: 2026-08-20
stub: false
---

## 2026-08-19 — collapsed 状态退役

`collapsed`/`setCollapsed`/`COLLAPSED_KEY`(localStorage
`artifact_column_collapsed`)删除——唯一写入者(列头折叠钮)v4 已删,唯一
读者(sliver 分支)同轮删除,见 [[ArtifactColumn]]。残留的 localStorage 旧值
无人再读,无迁移必要。

## 2026-08-12 (r2 review) — active 收敛到单一 helper + 注册表数组化

新增 `_pickVisibleActive(list, minimized, preferred)` 作为「谁是 active」的**单一真相**:remove + 两个 loader(loadForSession/loadPinned 各两分支)+ minimizeTab 全部过它,active 永不落在最小化 tab(此前 4 个 loader 写入点仍取 [0],刷新后配合 effectiveActiveId 从「显示错图」退化为「整列全白」——review Critical ①,治根因)。`chartInstances` 由单槽 `Record<id, T|null>` 改**数组** `Record<id, T[]>`:register 追加、unregister 按身份 filter、空则删键——修 modal 关闭后列内活实例被清空导致下载报 not ready(review ③);`|null` 死路径清除(铁律 #2)。upsert 的 focus 分支**不**走 helper(故意先取消最小化再聚焦)。

## 2026-08-12 — 0802 前端包:resize/注册表/active 不变量(bug ①②⑤)

`remove()` 新 active 过滤 minimizedTabIds(此前 list[0] 可指最小化 tab→整列空白),并顺带清 minimizedTabIds/chartInstances 该 id;新增 `unregisterChartInstance(id, instance)` 身份校验(只有当前 owner 能清槽);`minimizeTab`/`restoreTab` 补 `_promoteChartLru`(此前是唯二跳过 LRU 的路径,恢复到不在池里的 chart=无挂载空白)。

## 2026-07-23 — upsert focus option

`upsert(artifact, {focus?})`: `focus: true` forces activation (and
un-minimizes the tab) even when the artifact is already in the list.
Rationale: auto-focus previously only fired on `idx === -1`, so when a
list refresh (loadForSession) raced ahead of the register_artifact
tool_output roundtrip — or the agent re-registered an existing doc —
the panel stayed on the old tab (users read "still on Welcome" as
generation failure; prod feedback fp=07b73e74). [[ChatPanel.tsx]]'s
register signal now passes `focus: true`; `openUrl` keeps relying on
the default new-artifact auto-focus. Background-agent guard unchanged.

## 2026-07-22 — openUrl action

Added `openUrl(agentId, url, title?)` → `artifactsApi.openUrl` → `upsert`
(which auto-focuses the new tab). URL tabs are ordinary artifacts in the
store; no special state.
## 2026-05-19 — quotaError state removed + ECharts LRU added

Two related changes around the artifact pipeline cleanup (see
[[artifact_runner.py]] 2026-05-19 note):

- `quotaError` / `setQuotaError` are gone with the per-user quota. Tool-call
  failures during register_artifact now surface only as a structured
  `{error, code}` returned to the agent; no more layout-level modal.
- New `chartLruOrder: string[]` (newest first, length ≤ `CHART_LRU_LIMIT = 5`)
  + the `_promoteChartLru` helper. Every code path that flips
  `activeArtifactId` pipes the new id through `_promoteChartLru` so an
  echarts artifact rises to the head of the list. `ArtifactColumn` then
  keeps every id in that list mounted with `display: none` for the
  non-active ones, so re-selecting a recent chart is instant — no fetch,
  no `echarts.init`. Tail drops trigger ChartRenderer unmount which runs
  `chart.dispose()`. HTML / CSV / Markdown / PDF / image kinds slide
  through unchanged.

## 2026-05-14-r3 — `delete` no longer takes `deleteSource`

Deletion is registry-only; `delete(agentId, artifactId)` is the entire
surface. Workspace files are never touched. See the artifactsApi mirror
md for the rationale.

## 2026-05-14 — `delete` accepts `deleteSource`

`delete(agentId, artifactId, deleteSource?: boolean)` now passes the popup
choice down to `artifactsApi.remove`. `false` (default) tears down the DB
row only; `true` also `rmtree`s the artifact root directory in the agent
workspace. The popup itself lives in `ArtifactTabStrip` / `ArtifactsSection`
and decides which value to pass.

## 2026-05-08-r3 — WS subscription removed

`_ws`, `connectWs`, and `disconnectWs` have been removed from the store.
The dedicated `/ws/artifacts/{agentId}` endpoint was dropped because the
in-process `ArtifactEventBus` never delivered events across the MCP-process /
FastAPI-process boundary. Artifact signals now arrive exclusively through the
chat WebSocket stream (`tool_output` frames parsed in `ChatPanel.tsx`). The
store retains all REST-backed methods (`loadForSession`, `loadPinned`, `upsert`,
`remove`, `pin`, `delete`) — these are called by the chat WS handler and UI
components as before.

# artifactStore.ts — Artifact tab list, active selection, and collapse state

## Why it exists

The artifact panel is a persistent column alongside the chat panel. Its state (which artifacts are visible, which one is active, whether the column is collapsed) must survive component remounts and respond to server-push events without requiring a user action. A Zustand store outside the React tree is the right place for this.

## Upstream / Downstream

Reads from `services/artifactsApi.ts` for all HTTP operations. Subscribes to the backend WebSocket endpoint `ws://…/ws/artifacts/{agentId}` for real-time push events (artifact created, updated, pinned, deleted).

Consumed by (future) `ArtifactColumn.tsx` and `ArtifactViewer.tsx`. The `collapsed` field drives the CSS split-pane layout; `artifacts` drives the tab list; `activeArtifactId` drives which viewer is rendered.

## Design decisions

**Single flat `artifacts[]` list, not a session-keyed map.** Unlike `chatStore`, which needs per-agent independent streams, the artifact column is always scoped to one agent at a time (the currently open agent). A flat list is simpler and directly maps to "tabs rendered left to right."

**Pinned artifacts merged at the front of the list.** `loadForSession` fetches both pinned and session artifacts, deduplicates (pinned wins position), and merges them. This means pinned artifacts always appear first in the tab bar, regardless of when they were created.

**`upsert` auto-activates new artifacts.** When the WS fires `artifact.created`, `upsert` detects `idx === -1`, prepends the artifact, and sets it as active. When `artifact.updated` fires, `upsert` finds the existing entry and replaces it without changing `activeArtifactId`. This single method handles both cases with one conditional.

**`collapsed` persisted to `localStorage`.** User preference for panel collapse should survive page reload. The store initializes from `localStorage` (with a try/catch for SSR safety) and writes back on every `setCollapsed`. The key is `artifact_column_collapsed` and the value is `'1'` / `'0'` (string, for compatibility with `localStorage`'s string-only storage).

**WS lifecycle owned by the store, not a React hook.** `connectWs` and `disconnectWs` live in the store so that non-React code (e.g., a router-level effect) can manage the connection without needing a mounted component. The WS reference is stored as `_ws` (underscore prefix = internal, not for external consumers).

**`artifact.pinned` WS event mutates `session_id` to `null` when pinning.** When the backend pins an artifact it removes its session affiliation. The WS handler reflects this by setting `session_id: null` on the local copy when `evt.pinned` is truthy. Unpin restores the field from the backend's next PATCH response via `upsert` (the store does not reconstruct `session_id` on unpin from the WS event alone — `upsert` replaces the entry cleanly after the REST call).

## Gotchas

**`remove` reads `activeArtifactId` twice.** The remove action calls `get().activeArtifactId` inside the `set()` argument. Because Zustand's `set` is synchronous and `get()` reflects the current committed state (not the in-flight update), the second read of `activeArtifactId` inside the `set` argument may be stale if the artifact being removed is simultaneously set as active by another call. In practice this race does not occur because WS events are single-threaded in the browser.

**WS `artifact.deleted` calls `get().remove(evt.artifact_id)` even if the artifact was never loaded.** `remove` filters a non-existent ID safely (the filter returns the original list), so this is a no-op. No error.

**`disconnectWs` nulls out `onmessage` and `onclose` before calling `ws.close()`.** This prevents the `onclose` handler from writing `{ _ws: null }` to the store again after `disconnectWs` has already done so — avoiding a redundant state update and a potential double-close race.

**No reconnect logic.** If the WS drops mid-session (network blip), the store ends up with `_ws: null` and stops receiving push events. The next `connectWs(agentId)` call (triggered by the consumer when it detects stale state, or by a component re-mount) re-establishes the connection cleanly.

## 2026-08-18 — `applyEvent`:store 的唯一实时输入

后端推的 artifact_changed 事件在此落地(registered/updated/repointed→upsert,
deleted→remove+清理)。**updated_at 单调守卫**:迟到/重放事件(≥ 已知时间戳)
直接丢弃——重连时 event_stream 回放和全量拉会赛跑,守卫让顺序不再重要。
永不抛(坏事件 console.error 后丢弃,打开时全量拉兜底)。registered 走
upsert 新条目自动聚焦;updated/repointed 原地刷新不抢焦点(内容重载由
updated_at 经 useArtifactRawUrl refreshKey 驱动)。loadPinned 的刷新自
2026-08-18 拉 scope=context(自有 pinned ∪ team,与 agent 状态块同一可见
面)——方法名保留,因为"加载该 agent 的面板"语义未变。文件头那句「tool_output
frames parsed in ChatPanel」已过时,发现机制=事件+全量拉。

## 2026-08-19 — editorDirtyIds(dirty 守卫的视觉层)

编辑面 dirty 集合,TabStrip 圆点消费。内容安全**不**依赖它
(localStorage 草稿层才是兜底),纯视觉。

## 2026-08-20 — remove() 顺删编辑草稿键(#334 I8/r2 I5)

本地删除与 `applyEvent('deleted')` 都经 remove(),一处覆盖两路。
键模板/体积门的 owner 是 [[drafts]](lib/artifactEditing),store 只
消费 `removeDraft`——**不 import hooks**(否则 React 进 zustand 模块
图,store 纯函数测试变重)。
