---
code_file: frontend/src/components/layout/AgentList.tsx
last_verified: 2026-04-10
stub: false
---

# AgentList.tsx — Agent CRUD with real-time streaming + completion badges

## 为什么存在

The agent list is the primary navigation for multi-agent concurrent chat. It shows which agents are currently running (spinner), which have completed since you last viewed them (glowing dot badge), and lets you create, rename, delete, and toggle public/private.

## 上下游关系
- **被谁用**: `Sidebar`.
- **依赖谁**: `useConfigStore` (agents, agentId, setAgentId), `useChatStore` (isAgentStreaming, completedAgentIds, setActiveAgent, clearAgent), `api`.

## 设计决策

`completedAgentIds` in `useChatStore` tracks agents that have finished since you last visited them. Selecting an agent clears its completion badge via `setActiveAgent`. The badge is a small glowing dot overlaid on the agent icon.

Collapsed mode shows max 4 agents as icon squares — the rest are invisible but still selectable if you expand the sidebar.

Inline rename: clicking the pencil enters editing mode on that agent row. Enter/Escape confirms/cancels. The `editingAgentId !== agentId` guard ensures you cannot edit a different agent while the current one is selected for rename.

## 新人易踩的坑

`handleSelectAgent` always navigates back to `/app/chat` if the user is on a sub-page (Settings, System). This is intentional — clicking an agent always means "go talk to this agent".

Delete hits `api.deleteAgent` which cascades all related DB data server-side. There is no undo.
