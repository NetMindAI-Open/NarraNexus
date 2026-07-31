---
code_file: frontend/src/components/ui/index.ts
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 导出 `useNotice`

与 `useConfirm` 同一文件（[[ConfirmDialog]]）导出的通知封装。加在这里而不是让调用方深链
到具体文件，是这个 barrel 的既有约定。

# index.ts — Barrel export for the design-system primitives

Re-exports: `Button`, `Card` (+ `CardHeader/Content/Title/Footer`), `Input`, `Textarea`, `Badge`, `BetaBadge`, `ThemeToggle`, `Markdown`, `MarkdownPreview`, `Dialog` (+ `DialogContent/Footer`), `KPICard`, `KPIColor`.

Not re-exported here: `popover.tsx`, `scroll-area.tsx`, `tabs.tsx`, `tooltip.tsx`, `AgentCompletionToast`, `EmbeddingBanner`, `EmbeddingStatus`. Those are imported from their direct file paths by the specific consumers that need them.
