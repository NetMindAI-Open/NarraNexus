---
code_file: frontend/src/components/artifacts/ArtifactColumn.tsx
last_verified: 2026-05-19
stub: false
---

## 2026-05-19 — Live ECharts LRU pool

When the active artifact is an echarts kind, ArtifactColumn no longer
renders a single `<ArtifactRenderer artifact={active} />`. Instead it
iterates `chartLruOrder` (from [[artifactStore.ts]]) and renders every
id in that list, wrapped in `position: absolute inset-0` with
`display: block` for the active one and `display: none` for the rest.
Effect: clicking back to a recent chart costs nothing (no fetch, no
`echarts.init`); the oldest in the tail unmounts and its canvas is
disposed. Non-chart kinds still go through the single-renderer path.

## 2026-05-14 — Manual refresh button (expanded header + sliver)

Both modes gained a `RefreshCw` button → `artifactStore.loadPinned(agentId)`,
with a local `refreshing` flag spinning the icon. Rationale: artifacts are
**not** polled on a timer (event-driven — see `[[useAutoRefresh.ts]]`), so
this button is the user's explicit escape hatch to force a re-sync. The
normal paths are agent-complete `refreshAll` + the mid-stream `tool_output`
discovery in `[[ChatPanel.tsx]]`.

The **sliver** form was restructured from a single `<button>` into a
`<div>` holding two stacked buttons (expand + refresh) — a single button
couldn't host a nested refresh button. This matters most in the empty
state: that's exactly when the user wants to force a re-sync, and the
expanded-header refresh button isn't reachable then.

## 2026-05-14 — Renderer extraction + zoom modal + parent-driven flex width

- Renderer dispatch (`RENDERER_BY_KIND` + lazy imports) moved into
  `[[ArtifactRenderer]]` so the new fullscreen `[[ArtifactZoomModal]]`
  can reuse the exact same lazy chunks. `ArtifactColumn` now imports
  `ArtifactRenderer` and renders `<ArtifactRenderer artifact={active} />`
  for the body.
- New per-column `zoomedId` state and `<ArtifactZoomModal>` mounted at
  the bottom of the aside, **keyed by `zoomed?.artifact_id ?? 'closed'`**
  so each open is a fresh mount (lets the modal reset its zoom level
  via `useState` instead of an effect — see `[[ArtifactZoomModal]]`).
  Opens via:
  - "Zoom" button in the panel header (active artifact only).
  - "Zoom" icon on each tab in `ArtifactTabStrip`.
  - Double-click on a tab body.
- New optional `flexGrow` prop. When the parent layout passes it
  (expanded mode only), the aside switches from `flex-[2]` to
  `style={{ flexGrow, flexBasis: 0 }}` so the chat ↔ artifacts split
  in `MainLayout` can drive this column's width. Sliver mode continues
  to use the fixed `w-9` button — flexGrow is ignored there. The split
  only changes on divider-drag *release* (see `[[MainLayout]]` "Resize
  perf" — ghost-line drag), so this column re-renders once per drag,
  not per frame. (An interim version exposed a `forwardRef` for
  per-frame imperative writes; that was dropped when the drag switched
  to the ghost-line model.)

## 2026-05-13 — Always-visible sliver + auto-expand on new artifact

行为变化：之前 `artifacts.length === 0` 直接 `return null` 整个 unmount，
第一条 artifact 落地时面板"凭空冒出来"——视觉突兀、用户没有心理预期
也来不及反应。

改成：

1. **永远渲染**（去掉 length-0 的 early-return），0 件 artifact 时
   走极简的 sliver 形态——9px 宽的竖条带 "Artifacts" 标签 + chevron。
   用户从一开始就知道这一列存在、artifact 会出现在这里。
2. **新 artifact 到达自动展开**：useRef 跟踪上一帧长度，length
   增长且当前 collapsed 时调 `setCollapsed(false)`。mount 时
   prev=current 保证不会因为 stale-while-revalidate 缓存回填触发
   误展开——只在真正"长出新条目"时弹开。
3. **空态 sliver label** 不显示 `(0)`——纯 "Artifacts" + tooltip
   "Artifacts will appear here once the agent creates one"。

边界 case 取舍：用户手动 collapse 后又有新 artifact → 再次自动
展开。这是用户在 2026-05-13 review 时明确要的语义（"新增就自动
展开一下"）；如果实际用着烦再加 throttle / "only-on-first-growth"
开关。

`effectiveCollapsed = collapsed || artifacts.length === 0`：length 为
0 时强制 sliver，长度 ≥1 时尊重用户的 collapsed 偏好（auto-expand
useEffect 在 0→1 那一刻顺手把这个偏好翻成 false）。

# ArtifactColumn.tsx — 4th layout column for artifact rendering

## Why it exists

The agent layout has three primary columns (nav, chat, context). When the agent produces artifacts, a 4th column slides in to host the full-fidelity renderer. `ArtifactColumn` is the container that owns this column: it wires `ArtifactTabStrip` to the renderer dispatch table and manages the collapsed/expanded state that controls whether the 4th column occupies real estate at all.

## Upstream / Downstream

- **Placed by**: the main agent page layout, side-by-side with the chat panel.
- **Reads from**: `artifactStore` — `artifacts[]`, `activeArtifactId`, `collapsed`, `setCollapsed`.
- **Renders**: `ArtifactTabStrip` + a lazy renderer selected by `RENDERER_BY_KIND`.
- **Lazy imports**: `HtmlRenderer`, `ChartRenderer`, `CsvRenderer`, `ImageRenderer`, `MarkdownRenderer`, `PdfRenderer` — all via `React.lazy`.

## Renderer dispatch

Dispatch lives in `[[ArtifactRenderer]]` (extracted 2026-05-14). See that
file's md for the kind→renderer table and lazy-loading rationale.
`ArtifactColumn` is now just a consumer.

## Collapsed state

When `collapsed === true` and `artifacts.length > 0`, the column renders as an 8px-wide `<button>` sliver at the right edge of the layout. This allows the chat panel to reclaim horizontal space without destroying the artifact state. The collapsed state is persisted to `localStorage` via the store so it survives page refreshes.

When `artifacts.length === 0`, the component returns `null` — the 4th column is entirely absent from the DOM and does not consume layout space.

## Gotchas

**`[writing-mode:vertical-rl]` Tailwind arbitrary property**: The collapsed sliver button uses the Tailwind arbitrary-property syntax `[writing-mode:vertical-rl]` for vertical text. This is valid in any Tailwind v3+ project without any config additions. Do not use a custom utility class like `writing-mode-vertical` — it requires explicit `tailwind.config.js` registration and will be silently no-op if absent.

**Tab strip border**: `ArtifactTabStrip` renders `border-b` on its own outer container. The wrapper `<div>` in `ArtifactColumn` that holds the strip and the collapse button intentionally has **no** `border-b`. Adding it would produce a doubled hairline at the same pixel row.
