---
code_file: frontend/src/components/ui/AgentCompletionToast.tsx
last_verified: 2026-08-18
stub: false
---

# AgentCompletionToast.tsx — Bottom-right toast for background agent completions

## 2026-08-14 — 房间也会说话，于是 toast 变成了可辨识联合

团队房间是**异步空间**：把活交出去然后离开，这是设计意图。所以"房间开始说话了"正是
sidebar 圆点覆盖不到、而且最常见的那种情况——圆点只在用户**看 sidebar** 的时候才回答
问题。

队列原本是 agent 形状的：以 `agentId` 为键，"View" 切换当前 agent。团队 toast 既没有
可切换的 agent，也没有可以当键的 agent id。做成"agentId 为空字符串的 agent 项"会是
**一个字段两种含义**——正是让后续 bug 隐形的那种形状。于是 `ToastItem` 改成
`kind: 'agent' | 'team'` 的可辨识联合（见 [[chatStore.ts]]），键由 `toastKey` 生成
（`agent:<id>` / `team:<id>`），**按 kind 限定**，这样同名的 team 和 agent 无法互相
把对方的通知消掉。

两种 kind 的唯一实质差别是 "View" 去哪：agent 走 `setAgentId` + `setActiveAgent`，
房间走 `navigate('/app/teams/<id>/chat')`。所以这个分支只写在 `handleView` 一处，而
不是在渲染时到处推断。

放进队列的人：agent 由 [[chatStore.ts]] 的 `stopStreaming` 和 [[useAutoRefresh.ts]]
的后台消息检测推入；team 只有 [[useAutoRefresh.ts]] 的 `notifyWokenRooms`，触发条件
是**边沿**（用户已读完的房间开始说话）而不是电平——房间里六个 agent 同时回话时逐条
toast，是那种会被用户关掉的通知，而被关掉的功能比没做还糟。

顺手把 `"Completed"` / `"View"` 两处硬编码英文接进 i18n（`toast.*`）：它们是这个组件
里仅剩的未国际化文案。

## 为什么存在

Multi-agent concurrent chat: you can send a message to Agent B while looking at Agent A. When Agent B finishes, this toast appears so you know without having to poll the agent list. Clicking "View" switches the active agent and dismisses the toast.

## 上下游关系
- **被谁用**: Mounted once in `MainLayout` — always present, renders nothing when `toastQueue` is empty.
- **依赖谁**: `useChatStore` (toastQueue, dismissToast, setActiveAgent), `useConfigStore` (setAgentId).

## 设计决策

`toastQueue` is a store-managed array so multiple completions can stack. Each toast records its `timestamp` at creation; the auto-dismiss timer accounts for elapsed time so a toast that was already 4s old only waits 1 more second.

## Gotcha / 边界情况

This component is in `ui/` (not in `chat/`) because it sits in `MainLayout` alongside the Sidebar, not inside the chat panel. Placing it in `chat/` would couple the layout to the chat module.

## 2026-08-18 — artifact-repointed 渲染分支

status=warning(指针被动移动值得注意而非庆祝);View=restoreTab 把重指的
tab 带到前台;文案 i18n 键 toast.artifactRepointed{Verified,Unverified},
十语言已配。
