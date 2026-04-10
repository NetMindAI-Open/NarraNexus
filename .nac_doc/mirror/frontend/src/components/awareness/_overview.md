---
code_dir: frontend/src/components/awareness/
last_verified: 2026-04-10
stub: false
---

# awareness/ — Agent configuration panel (awareness text, social network, files, MCP)

## 目录角色

Renders the "Config" tab in the right context panel. Despite the directory name `awareness/`, the tab label in the UI is "Config". Contains four functional sections stacked vertically inside `AwarenessPanel`:

1. **Agent Awareness** — the agent's self-description text (editable via modal).
2. **Social Network** — contacts the agent has built up (sortable, keyword/semantic searchable).
3. **Workspace Files** — drag-and-drop file upload to the agent's file workspace.
4. **MCP Servers** — manage external MCP SSE endpoints.

`RAGUpload.tsx` exists but is no longer mounted — Gemini RAG was deprecated.

## 关键文件索引

| File | Role |
|------|------|
| `AwarenessPanel.tsx` | Orchestrator. Reads from `usePreloadStore`. Contains the edit-awareness modal. |
| `EntityCard.tsx` | Expandable row for a single social network contact. |
| `FileUpload.tsx` | Drag-and-drop file manager for agent workspace files. |
| `MCPManager.tsx` | CRUD + connection validation for external MCP SSE servers. |
| `RAGUpload.tsx` | Deprecated — not currently mounted anywhere. |

## 和外部目录的协作

- `usePreloadStore`: primary source for `awareness`, `socialNetworkList`, `chatHistoryEvents`.
- `useConfigStore`: `agentId`, `userId`, `clearAwarenessUpdate`.
- `api.updateAwareness`, `api.searchSocialNetwork`, `api.listFiles`, `api.uploadFile`, `api.deleteFile`, `api.listMCPs`, `api.createMCP`, etc.
