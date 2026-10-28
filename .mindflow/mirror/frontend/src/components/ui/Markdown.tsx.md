---
code_file: frontend/src/components/ui/Markdown.tsx
last_verified: 2026-08-14
stub: false
---

## 2026-08-14 (二) — rehypePlugins 用真类型

`unknown[]` + `as []` 改成 `PluggableList`（来自 `unified`）。`as []` 是转成"空元组"，
纯粹为了让 tsc 闭嘴——调用方传错东西不会被拦住，而这个 prop 的整个意义就是"把树交给别人
改"，最需要类型的地方反而没有。

`unified` 同时被声明进 `package.json`，见 [[rehypeMentions.ts]] 那条关于幽灵依赖的记录。

## 2026-08-14 — 可选的 rehypePlugins

新增 `rehypePlugins` prop（追加在 `rehypeRaw` 之后）。存在的理由是让调用方**改渲染树**而
不是**改 markdown 源码**：团队房间用正则在源码上高亮 @mention，结果把字面量 `<span>`
塞进了每一个提到 `@all` 的代码块——正则不知道什么是代码块，AST 知道。

传进来的数组必须**稳定**（模块常量或 `useMemo`）：这个组件按浅比较 memo，每次渲染新建一个
数组会让流式回复的每一个 delta 重新解析整段正文。

## 2026-07-30 — React.memo

remark/rehype 每次 render 都全量重解析，而聊天界面每个 WebSocket delta
都重渲染——未 memo 时一个 delta 会重解析**所有**可见历史气泡的 markdown，
主线程饱和表现为「流式卡住→整段一次性蹦出」。props 全是原始值，浅比较
即可跳过未变内容的重解析。

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
