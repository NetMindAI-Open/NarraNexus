---
code_file: frontend/src/lib/chatStreams.ts
last_verified: 2026-08-21
stub: false
---

## 为什么存在

对话与 Activity Log 是两条独立分页的后端流(simple-chat-history 的 `include`)。
`ChatPanel` 在三处 fetch 活动流(首屏 / load-more / 12s 轮询),把「当前 tab 用哪条流」
的判定收进一个纯函数 `streamForTab(tab)`,避免三处各写一遍 `chatTab === 'inner' ? ...`
而漂移(第二轮 review 的 per-stream 重构就一次改动了这三处周围的代码)。

## 契约

- `streamForTab('inner') === 'activity'`;其余 tab(`'conversation'`)→ `'chat'`。
- 纯函数、无副作用,单测在 `__tests__/chatStreams.test.ts`。

## 上下游

- **被谁用**:`ChatPanel.tsx`(`historyInclude = streamForTab(chatTab)`,喂三个 fetch 点)。
- 后端对应:`backend/routes/agents/chat_history.py` 的 `include=chat|activity|all`。
