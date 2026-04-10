---
code_dir: frontend/src/components/
last_verified: 2026-04-10
stub: false
---

# components/ — All React UI, organized by domain

## 目录角色

All React components live here, sliced into domain subdirectories. There is no `pages/` directory — React Router renders subdirectory entry points (`ChatPanel`, `AwarenessPanel`, …) directly through `MainLayout`'s right-panel tab system.

The directory has two tiers:
- **ui/** and **layout/** — the generic building blocks that everything else imports.
- **chat/, awareness/, inbox/, jobs/, runtime/, skills/, steps/, system/, cost/, settings/** — domain panels mounted as lazy children inside `ContextPanelContent`.

## 关键文件索引

| Path | Role |
|------|------|
| `layout/MainLayout.tsx` | Root shell: Sidebar + ChatPanel + right ContextPanel. The only place that owns the tab state (`ContextTab`). |
| `layout/ContextPanelContent.tsx` | Lazy-loads each panel on first tab activation; all heavy libs (ReactFlow, Markdown, …) are deferred here. |
| `layout/ContextPanelHeader.tsx` | Defines the `ContextTab` union type; owns tab notification badges (inbox unread count, awareness red dot). |
| `chat/ChatPanel.tsx` | The main chat surface; the most complex component in the codebase. |
| `ui/` | The design-system primitives — everything else imports from here, not from third-party lib paths. |

## 和外部目录的协作

- Stores: `useChatStore`, `useConfigStore`, `usePreloadStore`, `useRuntimeStore` (from `frontend/src/stores/`) are the primary data sources. Components almost never fetch directly — they use store selectors and call store actions.
- API: direct `api.*` calls happen only in leaf components when the data is not managed by a store (e.g., `FileUpload`, `MCPManager`, `AwarenessPanel` edit modal).
- Hooks: `useAgentWebSocket`, `useAutoRefresh`, `useTheme` from `frontend/src/hooks/`.
- Types: shared `SocialNetworkEntity`, `ChatMessage`, `FileInfo`, `MCPInfo`, `RAGFileInfo`, `EventLogResponse` from `frontend/src/types/`.
