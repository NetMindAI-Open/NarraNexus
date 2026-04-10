---
code_file: frontend/src/components/ui/index.ts
last_verified: 2026-04-10
stub: false
---

# index.ts — Barrel export for the design-system primitives

Re-exports: `Button`, `Card` (+ `CardHeader/Content/Title/Footer`), `Input`, `Textarea`, `Badge`, `ThemeToggle`, `Markdown`, `MarkdownPreview`, `Dialog` (+ `DialogContent/Footer`), `KPICard`, `KPIColor`.

Not re-exported here: `popover.tsx`, `scroll-area.tsx`, `tabs.tsx`, `tooltip.tsx`, `AgentCompletionToast`, `EmbeddingBanner`, `EmbeddingStatus`. Those are imported from their direct file paths by the specific consumers that need them.
