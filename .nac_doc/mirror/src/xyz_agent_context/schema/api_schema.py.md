---
code_file: src/xyz_agent_context/schema/api_schema.py
last_verified: 2026-04-10
stub: false
---

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
