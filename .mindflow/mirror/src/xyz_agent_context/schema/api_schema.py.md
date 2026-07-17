---
code_file: src/xyz_agent_context/schema/api_schema.py
last_verified: 2026-07-15
stub: false
---

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-07-10 — ClearHistoryResponse expanded for the scoped wipe

`ClearHistoryResponse` gained `scopes` + per-target counts (event_stream,
chat_memory, chat_instances, agent_messages, memory_rows, artifacts), disk
booleans and `disk_errors[]` — a projection of `WipeResult` from
[[wipe_service.py]]. Kept `success` True once the DB commits even if disk
deletes partially fail; `disk_errors` surfaces those.

## 2026-06-11 — identity fields dropped from request models

CreateAgentRequest.created_by, UpdateTimezoneRequest.user_id, UpdateOnboardingRequest.user_id removed — identity comes from auth_middleware exclusively (see routes/auth.py.md identity hardening entry).

## 2026-06-11 — RegisterRequest/RegisterResponse deleted; Login models slimmed

Register models gone with the endpoint. LoginRequest lost `password`, LoginResponse lost `token`/`role` — those fields only ever served the cloud password branch; local login never set them. Cloud login speaks NetmindLoginRequest/Response exclusively.

## 2026-06-11 — NetmindLoginRequest / NetmindLoginResponse

Request carries `netmind_token` (+ optional `source` entry-channel tag, e.g. "arena", consumed by Phase 2 provisioning). Response mirrors RegisterResponse's quota-seeding fields (has_system_quota / initial_*_tokens) so the frontend welcome toast survives the register->netmind-login switch, and adds display_name/email because user_id is now an opaque 32-hex userSystemCode unfit for display.

## 2026-05-21 — Onboarding schemas

Added `OnboardingProgress` / `OnboardingResponse` / `UpdateOnboardingRequest`
for the new-user onboarding checklist (see `backend/routes/auth.py.md`).
`OnboardingProgress` carries three write-once-true flags
(`first_agent_created`, `template_applied`, `dismissed`); it is stored
inside `users.metadata`, not as its own table. Also re-exported from
`schema/__init__.py` (both the import block and `__all__`).

## 2026-05-19 — AgentInfo gains last_assistant_preview / last_assistant_at

Two optional string fields added to `AgentInfo` so the frontend NM messenger sidebar can render "what did this agent last say" on rows the user hasn't opened in the current session — without first fetching that agent's chat history. The values are derived server-side in `routes/auth.py::get_agents` (one window-function SELECT over `events.final_output`) and are `None` for agents with no completed reply yet.

## 2026-05-15 — invite request DTOs removed

The short-lived `InviteRequestRequest` / `InviteRequestResponse` (added
2026-05-14 for the public `POST /api/invite/request` endpoint) are deleted.
After the architecture pivot — the public invite-request surface moved
to `narranexus-website` and NarraNexus exposes only the server-to-server
`POST /api/invite/internal/issue` — those DTOs no longer have a caller.
The new internal endpoint uses inline Pydantic models defined in
`backend/routes/invite.py` (private, single-caller).

## 2026-05-14 — FileInfo becomes a recursive tree node

`FileInfo` was flat (`filename`, `size`, `modified_at`). It now models a node
in the workspace **directory tree**: `name`, `path` (workspace-relative),
`is_dir`, `size`, `modified_at`, `children: Optional[List[FileInfo]]`.
Directories carry a `children` list (possibly empty); regular files carry
`children=None`. `FileListResponse.files` renamed to `tree`. Dotfolder
filtering is server-side — `FileInfo` never represents a hidden node.
`FileInfo.model_rebuild()` resolves the self-referential type hint. Pure
shape change (no backward compat); frontend is updated in the same change.

`FileDeleteResponse.filename` renamed to `path` because deletes accept nested
relative paths now.

# api_schema.py

## Why it exists

This file is the single source of truth for all HTTP request and response shapes exposed by `backend/routes/`. Rather than scattering inline `BaseModel` definitions across route files, api_schema.py centralizes them so that the frontend TypeScript types can be generated or manually aligned against one file. Every model here is a DTO (data transfer object) — it has no database storage of its own and no business logic.

## Upstream / Downstream

The route handlers in `backend/routes/` (agents, users, chat, jobs, mcp, files, costs) import only from this file for their request validation and response construction. The models in this file know nothing about the internal domain models (`Narrative`, `ModuleInstance`, `Event`) — that translation happens inside the route handlers themselves. The frontend `src/types/` TypeScript interfaces are the consumers on the other side of the wire.

## Design decisions

**Why not generate TypeScript types automatically from these Pydantic models?** The project is fast-moving; schema generation tooling adds a build step that slows iteration. The current contract is maintained by convention — keep the Pydantic models in sync with the TypeScript interfaces manually.

**`NarrativeInfo` and `InstanceInfo` duplicated from internal domain models**: these are presentation-layer projections, not the same objects as `Narrative` from `narrative/models.py`. They contain only the fields the frontend needs and in string-friendly formats (datetimes serialized as strings). Unifying them with the domain models was considered but rejected because the domain models carry internal state (embeddings, raw JSON fields) that should never leave the server.

**`SimpleChatHistoryResponse` vs `ChatHistoryResponse`**: the "simple" variant was added later to give the frontend a flat chronological message list without grouping by Narrative. The structured variant (`ChatHistoryResponse`) is used by the chat history panel that shows Narrative-grouped context. Both exist because the two UI panels have genuinely different data needs.

**`CostSummary` / `CostRecord`**: these are read-only analytics types with no corresponding write endpoint. They are produced entirely by aggregation queries in the cost route handler.

## Gotchas

**`DeleteAgentResponse.deleted_counts`** is a dict mapping table name to count. The keys are not stable strings declared anywhere — they are whatever the route handler decides to include. If you are writing a frontend assertion against specific keys, check the route implementation, not this schema.

**`SimpleChatMessage.working_source`** can be `"chat"`, `"job"`, `"matrix"`, or any other `WorkingSource` string value. It is stored as a raw string here (not the `WorkingSource` enum) because this DTO is agnostic to the internal enum definition.

**`RAGFileInfo.upload_status`** values (`"pending"`, `"uploading"`, `"completed"`, `"failed"`) are not defined as an enum here; they are just strings. The Gemini RAG module drives these states internally.

## New-joiner traps

- `AgentInfo.bootstrap_active` is a runtime flag, not a stored field. It is computed at request time by checking whether the agent's awareness module has a bootstrap mode active. Do not look for it in the database.
- `MCPInfo` here and `MCPUrl` in `entity_schema.py` represent the same underlying database record. `MCPUrl` is the domain entity; `MCPInfo` is the API projection with some fields stringified and some omitted.
- `EventLogResponse` is loaded on-demand (lazy loading) — the chat history endpoint returns `event_id` in each `SimpleChatMessage` so the frontend can fetch the full tool call trace separately, avoiding large payloads on the initial load.
