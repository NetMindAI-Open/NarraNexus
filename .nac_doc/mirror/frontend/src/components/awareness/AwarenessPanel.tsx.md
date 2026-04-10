---
code_file: frontend/src/components/awareness/AwarenessPanel.tsx
last_verified: 2026-04-10
stub: false
---

# AwarenessPanel.tsx — Agent configuration hub (awareness + social network + files + MCP)

## 为什么存在

The "Config" tab gives operators visibility into and control over the agent's current state: what it knows about itself (awareness text), who it knows (social network), what files it can access, and which external MCP servers are connected.

## 上下游关系
- **被谁用**: `ContextPanelContent` (lazy-loaded when 'awareness' tab is active).
- **依赖谁**: `EntityCard`, `FileUpload`, `MCPManager`, `usePreloadStore`, `useConfigStore`, `api`.

## 设计决策

**Awareness text editing**: Done in a `Dialog` modal (not inline) to avoid layout shifts and to provide a proper multi-line edit experience. On save, calls `api.updateAwareness` then re-fetches.

**Social network sort**: Entities are sorted: current user first, then by actual chat count derived from `chatHistoryEvents`. This count is recalculated with `useMemo` each render but the data comes from preloaded `chatHistoryEvents` (no extra API call).

**Semantic vs keyword search**: Both modes call `api.searchSocialNetwork`. Search results replace the sorted list while `hasSearched` is true; the original list reappears after clearing.

**Red dot clearing**: On mount, calls `clearAwarenessUpdate(agentId)` to dismiss the notification dot in the tab header. This means opening the Config tab is treated as "acknowledged".

## Gotcha / 边界情况

`RAGUpload` is imported but not rendered — there's a comment in the source: "RAG Upload Section removed — Gemini RAG deprecated". The import should be cleaned up.

KPI metrics row uses `KPICard` from `@/components/ui` but the network stats are calculated inline here, not from the store.
