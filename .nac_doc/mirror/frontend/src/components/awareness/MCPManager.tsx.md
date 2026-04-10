---
code_file: frontend/src/components/awareness/MCPManager.tsx
last_verified: 2026-04-10
stub: false
---

# MCPManager.tsx — External MCP SSE server management with connection validation

## 为什么存在

The agent runtime can connect to external MCP servers (SSE protocol) for additional tools. This component lets operators add, remove, enable/disable, and validate connectivity of those servers without restarting anything.

## 上下游关系
- **被谁用**: `AwarenessPanel`.
- **依赖谁**: `api.listMCPs`, `api.createMCP`, `api.deleteMCP`, `api.updateMCP`, `api.validateMCP`, `api.validateAllMCPs`.

## 设计决策

Auto-validation on load: when MCPs are first fetched and any have `connection_status === 'unknown'`, `validateAll()` is triggered automatically. This gives a live status view without requiring the user to click "Refresh".

The badge shows `connected/total` count (e.g., `2/3`).

`MCPItem` is a private sub-component in this file — no separate file, since it has no other consumers.

## Gotcha / 边界情况

`validateAll` is called from a `useEffect` that watches `mcps.length` — not `mcps` directly. This avoids re-triggering validation every time a status update modifies the `mcps` array. However it also means if a newly-added MCP doesn't have `unknown` status, the effect won't fire.
