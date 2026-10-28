---
code_file: frontend/src/components/artifacts/ArtifactInlineBadge.tsx
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — setCollapsed 死调用移除

`open()` 里的 `setCollapsed(false)` 删除——v4 后 artifactStore 的 `collapsed`
无人再读,该写入是空操作。点击行为不变:`restoreTab(artifact_id)` 独自完成
打开。与 [[ArtifactPreviewCard]] 的同名调用同轮删除,详见
[[ArtifactColumn]]/[[artifactStore]] 08-19 条。

## 2026-08-12 — 0802 前端包:resize/注册表/active 不变量(bug ①②⑤)

点击激活由 `setActive` 改 `restoreTab`(目标可能最小化,active 指隐藏 tab 会白屏——与 NewTabOmnibox 对齐)。

# ArtifactInlineBadge.tsx — minimal artifact pointer chip

## Why it exists

Chat used to render a full `ArtifactPreviewCard` (thumbnail + CSV head / image / markdown preview) underneath any `register_artifact` tool call. Two real-world problems:

1. **Visual flash.** Re-register (the agent's refresh signal — same `artifact_id`, bumped `updated_at`) re-mounted the card. Every iteration on a single artifact made the card visibly redraw, distracting the reader. Several users described it as "a little box that flashes and disappears".
2. **Transient.** Tool-call data is not persisted in the chat history table — `buildTimeline.ts` drops it on history rows. After history reload, the card vanished entirely. Felt like a bug ("where did my artifact go?").

This badge replaces the card with a single-line chip: paperclip icon + title + `↗`. No raw-URL fetch. No content preview. One job: click to focus the artifact tab in `ArtifactColumn`.

## How it relates to ArtifactPreviewCard

`ArtifactPreviewCard` is still exported from `components/artifacts/index.ts` for potential future use (e.g. a dedicated artifact list view). Chat does NOT mount it; only `ArtifactInlineBadge` is wired from `ChatPanel`'s `ArtifactToolCallCards` helper.

## Persistence caveat

The badge still relies on `toolCalls` being present on the timeline item. Chat history rows from the DB do not carry tool calls (decision: 2026-05-15 — don't migrate schema for this), so after a history reload the badge disappears on those messages. This is accepted: the artifact itself stays accessible via `ArtifactColumn` and the Settings → Artifacts list.
