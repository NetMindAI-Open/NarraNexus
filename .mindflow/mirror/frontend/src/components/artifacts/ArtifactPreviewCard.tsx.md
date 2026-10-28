---
code_file: frontend/src/components/artifacts/ArtifactPreviewCard.tsx
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 改查 `ARTIFACT_KINDS` 注册表并订阅

`KIND_REGISTRY[kind]` → `ARTIFACT_KINDS.get(kind)`，并 `useRegistryEntries(ARTIFACT_KINDS)` 订阅：插件在首屏之后
注册的 kind 会让已打开的视图重渲染，而不是停在「不支持的类型」。

## 2026-08-19 — setCollapsed 死调用移除(下方 08-12 条的调用顺序不变量随之失效)

`open()` 里的 `setCollapsed(false)` 删除——v4 后 artifactStore 的 `collapsed`
无人再读,该写入是空操作(见 [[ArtifactColumn]]/[[artifactStore]] 08-19 条)。
点击行为不变:`restoreTab(artifact_id)` 独自完成打开。下方 08-12 条里
「setCollapsed(false) 必须先于 setActive()」的顺序契约与「Reads from
setCollapsed」「经 setCollapsed 间接唤醒 ArtifactColumn」的描述**已随删除
失效**,以本条为准。[[ArtifactInlineBadge]] 的同名调用同轮删除。

## 2026-08-12 — 0802 前端包:resize/注册表/active 不变量(bug ①②⑤)

同 InlineBadge:激活改 `restoreTab`,防激活到最小化 tab。

## 2026-05-15 — no longer mounted from chat

`ChatPanel` switched to `ArtifactInlineBadge` (a one-line chip). The card is still exported from `components/artifacts/index.ts` but currently has no mount site — kept as a building block in case a future "artifact list with thumbnails" view wants it. Re-introducing it in chat is discouraged: the visible flash on re-register is the exact symptom users complained about.

## 2026-05-14 — pointer model

The card mints a view token via `useArtifactRawUrl` and fetches the
thumbnail content (csv head / markdown head / image bytes) from the
token-protected public URL — no `Authorization` header is attached. Drops
the `latest_version` dependency from the effect's deps array; it now keys
off the minted URL instead.

# ArtifactPreviewCard.tsx — Inline chat thumbnail for artifact references

## Why it exists

When the agent emits a tool result that creates or references an artifact, the chat thread needs a compact, tappable summary so the user knows what was produced without having to navigate away. This card bridges the chat column and the artifact column: it renders a preview in-place and, on click, focuses the ArtifactColumn on the matching tab.

## Upstream / Downstream

- **Rendered by**: `MessageBubble` (or a future ToolResultBlock) when a tool result payload includes an `artifact_id`.
- **Reads from**: `artifactStore` (for `setActive` / `setCollapsed`). Also makes a direct `fetch()` call to `rawUrl()` for csv/markdown thumbnails.
- **Activates**: `ArtifactColumn` indirectly, via `setCollapsed(false)` + `setActive(artifact_id)`.

## Thumbnail strategy

| Kind | What is shown | Why |
|------|--------------|-----|
| `image/png` / `image/jpeg` | `<img>` pointing at rawUrl | Native browser image decode, no extra fetch needed beyond the img request itself |
| `text/csv` | First 5 rows × first 5 columns via `fetch().then(text)` | Gives at-a-glance data preview; full render needs CsvRenderer |
| `text/markdown` | First 200 chars of the raw text | Fast, avoids pulling in a Markdown renderer just for a thumbnail |
| `application/vnd.echarts+json` | Placeholder label | ECharts requires a full canvas + JS bundle; pointless to partially instantiate in a thumbnail |
| `text/html` | Placeholder label | Same sandboxing complexity as full HtmlRenderer; a thumbnail iframe adds no value |
| `application/pdf` | Placeholder label | Browser PDF viewer must be full-size; thumbnail is not meaningful |

## Design decisions

**Eager fetch for csv/markdown only**: The `useEffect` fires only for the two kinds where a plain text fetch is sufficient to produce a useful thumbnail. Image kinds use a plain `<img>` tag (the browser manages the request). All other kinds skip network activity entirely.

**Click handler order**: `setCollapsed(false)` must be called before `setActive()`. If the column is collapsed and `setActive` fires first, the tab strip might re-render into a collapsed state that is immediately replaced — the ordering avoids a flash.

**No `rounded` corners**: The rest of the UI uses sharp right-angle rectangles (Nordic archive aesthetic). The card uses `border border-[var(--border-default)]` with no border-radius to match.

## Gotchas

The `useEffect` dependency array uses stable scalar fields `(kind, agent_id, artifact_id, latest_version)` instead of the full `artifact` object. This prevents refetch storms when the parent re-renders with a new object reference but identical data. The four fields are sufficient to uniquely identify both the artifact and the version being previewed.

**Error path (I3, 2026-05-09)**: The fetch chain now checks `r.ok` and catches network errors. A `previewError` state slot stores the error string; when set, a small red fallback line renders below the thumbnail area. The effect uses an async IIFE (matching ChartRenderer's pattern) so that the `setPreviewError(null)` reset and the async `setPreviewError(String(e))` are in the same async microtask batch — required by `react-hooks/set-state-in-effect` (eslint-plugin-react-hooks v7).

**No spinner-forever bug**: Previously, a failed fetch would leave csvHead/mdHead as null with no error indicator, producing an empty 80px div with no feedback. The error path makes the failure visible.

## 2026-08-19 — kind 分支改查注册表

`kind ===` 链换成 `KIND_REGISTRY[kind].preview` 策略
(image/csv-head/md-head/placeholder/none)+ `previewPlaceholderKey`。
行为逐一保持(office-live/x-url 依旧无预览体)。加 kind 的预览形态
在注册表声明,本组件不再改。
