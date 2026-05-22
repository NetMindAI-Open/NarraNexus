---
code_file: frontend/src/components/skills/MCPManager.tsx
last_verified: 2026-05-22
stub: false
---

## 2026-05-22 — add-form usability hints (#4)

The "I added an MCP but the agent can't use it" complaint was verified e2e
(2026-05-22) to NOT be a platform bug — adding a well-formed remote SSE MCP and
running the agent works (it calls the tool). The failures are almost always the
user's URL: not an SSE endpoint, unreachable, needs auth, or a stdio/npx local
MCP (unsupported). `AddMCPForm` now spells out that contract as a hint list, and
the status-dot tooltip shows the FULL `last_error` (was truncated to 50 chars)
so a Failed validation is self-explanatory. Pure copy/UX — no behavior change.

# MCPManager.tsx — External MCP SSE server management with connection validation

## 为什么存在

The agent runtime can connect to external MCP servers (SSE protocol) for additional tools. This component lets operators add, remove, enable/disable, and validate connectivity of those servers without restarting anything.

## 2026-05-14 — Relocated awareness/ → skills/

Moved from `components/awareness/` to `components/skills/`. MCP servers are a
tool/capability concern, so they now live next to Skills and render inside
`[[SkillsPanel]]` (the "Skill & MCP" tab) instead of the Config panel. The
component body is unchanged — all imports were already `@/`-aliased so the
move was purely structural.

## 上下游关系
- **被谁用**: `[[SkillsPanel]]`.
- **依赖谁**: `api.listMCPs`, `api.createMCP`, `api.deleteMCP`, `api.updateMCP`, `api.validateMCP`, `api.validateAllMCPs`.

## 设计决策

Auto-validation on load: when MCPs are first fetched and any have `connection_status === 'unknown'`, `validateAll()` is triggered automatically. This gives a live status view without requiring the user to click "Refresh".

The badge shows `connected/total` count (e.g., `2/3`).

`MCPItem` is a private sub-component in this file — no separate file, since it has no other consumers.

## Gotcha / 边界情况

`validateAll` is called from a `useEffect` that watches `mcps.length` — not `mcps` directly. This avoids re-triggering validation every time a status update modifies the `mcps` array. However it also means if a newly-added MCP doesn't have `unknown` status, the effect won't fire.
