---
code_file: frontend/src/components/ui/Markdown.tsx
last_verified: 2026-04-10
stub: false
---

# Markdown.tsx — react-markdown wrapper with GFM, raw HTML, and external-link handling

## 为什么存在

Central point for Markdown rendering so every surface (chat bubbles, awareness text, inbox messages, entity descriptions) gets consistent typography and the same external-link `target="_blank"` behavior.

## 上下游关系
- **被谁用**: `MessageBubble`, `AwarenessPanel`, `InboxPanel`, `AgentInboxPanel`, `EntityCard`.
- **依赖谁**: `react-markdown`, `remark-gfm`, `rehype-raw`.

## 设计决策

`rehype-raw` is enabled — the agent backend can send HTML inside Markdown (e.g., from job reports). This is intentional but means XSS is possible if untrusted content is rendered. Current threat model: content comes only from the user's own agents.

`compact` mode adds `markdown-compact` CSS class — the actual compact styles live in a global stylesheet, not in this file.

## Gotcha / 边界情况

`MarkdownPreview` truncates at `maxLength` characters of the raw Markdown string, not rendered length — the truncation point may fall in the middle of a Markdown construct and produce broken rendering.
