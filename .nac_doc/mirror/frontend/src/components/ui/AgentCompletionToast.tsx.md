---
code_file: frontend/src/components/ui/AgentCompletionToast.tsx
last_verified: 2026-04-10
stub: false
---

# AgentCompletionToast.tsx — Bottom-right toast for background agent completions

## 为什么存在

Multi-agent concurrent chat: you can send a message to Agent B while looking at Agent A. When Agent B finishes, this toast appears so you know without having to poll the agent list. Clicking "View" switches the active agent and dismisses the toast.

## 上下游关系
- **被谁用**: Mounted once in `MainLayout` — always present, renders nothing when `toastQueue` is empty.
- **依赖谁**: `useChatStore` (toastQueue, dismissToast, setActiveAgent), `useConfigStore` (setAgentId).

## 设计决策

`toastQueue` is a store-managed array so multiple completions can stack. Each toast records its `timestamp` at creation; the auto-dismiss timer accounts for elapsed time so a toast that was already 4s old only waits 1 more second.

## Gotcha / 边界情况

This component is in `ui/` (not in `chat/`) because it sits in `MainLayout` alongside the Sidebar, not inside the chat panel. Placing it in `chat/` would couple the layout to the chat module.
