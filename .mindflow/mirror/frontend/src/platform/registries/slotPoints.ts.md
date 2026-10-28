---
code_file: frontend/src/platform/registries/slotPoints.ts
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

注明 bare tool name 是**全局键**：`mcp__chat__read` 与 `mcp__browser__read` 共享 `read`，只注册跨模块
唯一的名字；注册表本身拒绝第二个认领者。

## 2026-09-24 ui.toolRenderers（第 18 个注册表）

新增内容注册表 `TOOL_RENDERERS`（`ui.toolRenderers`）：接管**某一个工具的输出行**。条目 id 是去掉
`mcp__<server>__` 的裸工具名——插件不必知道部署把它的工具挂在哪个 MCP server id 下。`accepts(output)`
让渲染器拒绝读不懂的输出形状；`toolRendererFor` 统一做「找条目 + accepts（抛错视为拒绝）」。没注册、
拒绝、渲染崩溃三种情况都回落 shell 的通用输出行，输出永远不会被插件藏掉。与 timelineEvents 的区别：
那个管 shell **不认识**的事件类型；这个管 shell 认识的 tool_output 里、某个具体工具的呈现。

# registries/slotPoints.ts — slot points and content registries

## Intent

Where a plugin adds UI *inside* surfaces the shell already draws: seven slot points (`conversationKinds`, `chatHeaderActions`, `composerExtensions`, `messageActions`, `sidebarSections`, `agentCardBadges`, `topBarItems`) plus the two content registries (`messageRenderers`: own the bubble of a message you recognise; `timelineEvents`: render a timeline event type the shell does not know). Component slots take `{component, when, order}`, action slots `{label, run(ctx), when, order}`; the `validated()` wrapper parses `when` at registration. `visibleSlotEntries` (filter + sort) and `rendererFor` (lowest-order match, a throwing matcher counts as no match) are the two host-side reads. The shell registers `chat` as a conversation kind, builtin.teams `team`.

## 2026-09-07 — `validated()` uses `Registry`'s `validate` option (M-11)

`validated()` used to overwrite the returned instance's OWN `register` property
(`registry.register = (id, value, options) => {...}`) — one of two functionally-identical
"validating registry" patterns in this codebase (`themes.ts` subclassed `Registry` instead). Both
now use `Registry`'s constructor-level `validate` option (see `registry.ts`'s mirror doc);
`validated()` is now just `new Registry<T>(kind, { validate: (value) => parseWhen(value.when) })`.
