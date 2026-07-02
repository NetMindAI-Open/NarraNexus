---
code_file: frontend/src/components/layout/MainLayout.tsx
last_verified: 2026-06-23
stub: false
---

## 2026-06-23 — TeamChatView in the main slot

Added `TeamChatView` (wraps [[TeamChatPanel]]). The route `/app/teams/:id/chat`
is matched off `location.pathname` and rendered in the SAME main slot as
`ChatView` — NOT as a sub-page Outlet with the close-X — so switching between a
single agent and a team's group chat feels seamless. `isSubPage` excludes the
team-chat path.

## 2026-06-11 — atomic-tab drawer

Owner IA revision: drawerTab is now an AtomicTabId (one tab = one
panel); focusKey deep-linking removed (the atomic tab IS the
destination). Drawer content renders via [[BookmarkPanelHost]] (lazy
per panel — the click-latency fix); title from the [[tabs]] registry.

## 2026-06-10 — Context column retired; bookmark strip + drawer

The permanent right context column (ContextPanelHeader/Content, 5 tabs)
is gone. ChatView now renders, right of the chat+artifact group:
optional pinned-drawer column → [[BookmarkStrip]] (~36px, always) →
slide-over [[BookmarkDrawer]] (portal, default). Drawer state lives
here: `drawerTab` / `drawerFocusKey` / `drawerPinned` (persisted under
`bookmark_drawer_pinned_v1`); first open writes
`bookmark_drawer_opened_v1` for the onboarding step. Chat+artifact
group went flex-[5] → flex-1; [[useBookmarkSignals]] is mounted here;
CostPopover moved to the chat card's top-right corner. Re-clicking the
open big bookmark toggles the drawer closed.

## 2026-05-21 — onboarding checklist above the chat

The chat-column card became a `flex flex-col` hosting `<OnboardingChecklist/>`
on top + `<ChatPanel/>` in a `flex-1 min-h-0` wrapper. The checklist is
cloud-only and self-hiding (renders null when not applicable / dismissed),
so when it's absent the layout is byte-identical to before — ChatPanel
just fills the column. The `min-h-0` wrapper is required so ChatPanel's
`h-full` still resolves once a sibling is above it.

## 2026-05-14 — User-resizable chat ↔ artifacts split

- Chat column and `ArtifactColumn` now live inside a shared `flex-[5]`
  inner group; the legacy `flex-[3]` / `flex-[2]` on each was the
  default 3:2 ratio in disguise, so the joint share stays at 5 and the
  Context column's `flex-[2]` is untouched.
- New `chatSplit` state (fraction of joint area occupied by chat),
  default 0.6 — equivalent to the legacy ratio. Persisted in
  `localStorage` under the key `chat_artifact_split_v1` so refresh
  preserves the user's choice.
- `[[ResizableDivider]]` is rendered between the two panes.
- The divider is **only rendered when the artifact column is in
  expanded mode** (`agentId && artifacts.length > 0 && !collapsed`).
  In sliver mode the artifact pane is a fixed 36-px button and
  resizing it would be pointless / misleading.
- `ArtifactColumn` accepts the optional `flexGrow` prop and switches to
  `style={{ flexGrow, flexBasis: 0 }}` when set. The legacy `flex-[2]`
  is kept as the fallback in case someone renders the column directly
  without `MainLayout`.

### Resize perf — ghost-line drag, commit on release (2026-05-14, 2 iterations)

**Iteration 1** moved the columns imperatively during the drag (wrote
`flexGrow` straight to the DOM, no React render). That killed the
React-render cost, but the columns *still physically resized* every
frame — and resizing the artifact pane reflows whatever it hosts. An
HTML artifact is a sandboxed `<iframe>`; reflowing it 60×/s, especially
while **shrinking**, was still visibly janky.

**Iteration 2 (current)** stops moving the columns during the drag
entirely. Only a thin "ghost" preview line tracks the cursor:
- `computeSplit(clientX)` — pure helper, maps pointer X against the
  group's `getBoundingClientRect()` to a clamped fraction (honours the
  `MIN_CHAT_PX` / `MIN_ARTIFACT_PX` per-pane minimums).
- `handleResize` — the divider's `onResize` (rAF-coalesced). Sets the
  `ghostLineRef` element's `left` + `display:block`. The real columns
  are **not touched** → zero reflow during the drag. Stashes the value
  in `pendingSplitRef`.
- `handleResizeEnd` — the divider's `onResizeEnd`. Hides the ghost line
  and does one `setChatSplit` → one re-render → the columns resize and
  their content reflows **exactly once**, and the persist `useEffect`
  fires.
- The ghost line is an `absolute`-positioned `<div>` inside the (now
  `relative`) chat+artifact group; it only renders alongside the
  divider (expanded mode). `ArtifactColumn` is back to a plain function
  component — the `forwardRef` from iteration 1 is gone, nothing needs
  a DOM handle to it anymore.

## v2.3-r3 改动（2026-05-08-r3）

- **WS lifecycle removed**: `connectWs(agentId)` and `disconnectWs()` calls removed from the `useEffect`. The dedicated `/ws/artifacts/{agentId}` endpoint was dropped; artifact signals arrive via the chat WS stream (`tool_output` frames in `ChatPanel.tsx`).
- `loadPinned(agentId)` is still called on mount/agent-change to hydrate agent-scoped artifacts.
- The `connectWs` and `disconnectWs` selectors are no longer imported from `useArtifactStore`.

## v2.3 改动（2026-05-08）

- **4-column layout**: `ChatView` now renders `<ArtifactColumn agentId={agentId} />` between the chat column and the context column. `ArtifactColumn` auto-hides when no artifacts are loaded, so the layout degrades gracefully to 3 columns for agents that don't produce artifacts.
- **Session-ID gap**: `chatStore` does not expose a per-agent session ID (`AgentChatState` has no `sessionId` field). `loadForSession` is intentionally not called. If a session-ID source is added to `chatStore` in the future, add a `loadForSession(agentId, sessionId)` call here.
- **ASCII diagram updated**: file header now shows 4 columns.

## v2.2 改动（2026-04-13）

- **G1 内层 Suspense**：`<Outlet />` 包了 `<Suspense fallback={<DashboardSkeleton />}>`。理由：App.tsx 外层 Suspense 一旦触发会全屏覆盖（包括 Sidebar），用户感受是"点击后整页消失"。内层 Suspense 把 fallback 限在主内容区，Sidebar 始终可见 → 慢导航问题修复。
- DashboardSkeleton 的形状刻意 mimic 真实 dashboard grid，避免 swap 时跳行。

# MainLayout.tsx — Four-column app shell and React Router layout

## 为什么存在

React Router renders this as the layout wrapper for all `/app/*` routes. It decides whether to show the default `ChatView` (chat + artifact column + right panel) or delegate to `<Outlet />` for sub-pages like Settings and System.

## 上下游关系
- **被谁用**: React Router route config (`/app` layout route).
- **依赖谁**: `Sidebar`, `ChatPanel`, `ArtifactColumn`, `ContextPanelHeader`, `ContextPanelContent`, `AgentCompletionToast`, `usePreloadStore`, `useConfigStore`, `useArtifactStore`, `useAutoRefresh`.

## 设计决策

`isSubPage` is detected by checking `location.pathname` — if not `/app/chat` or `/app`, the `<Outlet />` gets the full remaining width with no right panel. This avoids needing a nested router layout for each sub-page.

`ChatView` is a separate named export (not inline) so it can be referenced without instantiating `MainLayout`. It owns the `ContextTab` state — the right-panel tab selection does not persist across navigation.

`preloadAll` is called whenever `agentId` or `userId` changes. This is the entry-point for populating `usePreloadStore` with awareness, social network, inbox, and other agent-specific data.

The artifact WS lifecycle (`connectWs` / `disconnectWs`) lives in `ChatView`'s `useEffect(_, [agentId])`. This ensures the artifact stream tracks the currently selected agent and is torn down on agent switch or unmount.

## Gotcha / 边界情况

`onAgentComplete` is passed to `ChatPanel` as a callback that calls `refreshAll` from `useAutoRefresh`. This is the mechanism that refreshes the right-panel data after an agent run finishes.

**Right-panel height chain (must keep `flex flex-col` on the framed wrapper)**: the `<div>` that wraps `<ContextPanelContent>` carries both `overflow-hidden` (visual clipping for the bordered frame) and `flex flex-col` (so the chain flex-1 → ContextPanelContent's flex-1 → Card's h-full → CardContent's overflow-y-auto can resolve a real height). Removing `flex flex-col` breaks the chain — every right-side panel renders at content height, gets clipped, and the user sees a "tab won't scroll" bug. There is no React state hint in this file telling future editors that the className is load-bearing; this paragraph is the warning.

**ArtifactColumn conditional rendering**: `{agentId && <ArtifactColumn agentId={agentId} />}` — when `agentId` is falsy (no agent selected), the column is omitted entirely. Inside `ArtifactColumn`, if `artifacts.length === 0`, it also returns `null`. The net result: the column only occupies layout space when there is both an active agent AND at least one artifact loaded.
