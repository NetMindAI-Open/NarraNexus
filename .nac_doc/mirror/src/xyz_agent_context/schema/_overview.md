---
code_dir: src/xyz_agent_context/schema/
last_verified: 2026-04-10
stub: false
---

# schema/

## Directory role

All Pydantic data models and dataclasses live here. The schema layer has no business logic, no database access, and no imports from other layers of this package. It is the shared vocabulary that every other layer speaks.

Files split along two axes: **persistence** (what goes in the database) vs **runtime** (in-memory contracts) and **domain** (business concepts) vs **protocol** (wire formats).

## Key file index

| File | What it models |
|---|---|
| `entity_schema.py` | Core DB entities: Agent, User, SocialNetworkEntity, MCPUrl |
| `instance_schema.py` | ModuleInstance lifecycle (DB record + runtime object + narrative link) |
| `module_schema.py` | Module static config (ModuleConfig, MCPServerConfig) + legacy ModuleInstance |
| `context_schema.py` | ContextData accumulator and ContextRuntimeOutput |
| `hook_schema.py` | HookAfterExecutionParams and WorkingSource enum |
| `decision_schema.py` | Step-2 decision outputs: ModuleLoadResult, PathExecutionResult |
| `runtime_message.py` | Streaming message types yielded by AgentRuntime |
| `job_schema.py` | JobModel, TriggerConfig, JobExecutionResult |
| `inbox_schema.py` | InboxMessage for agent-to-user notifications |
| `agent_message_schema.py` | AgentMessage for the chat message audit trail |
| `rag_store_schema.py` | RAGStoreModel for Gemini knowledge base metadata |
| `channel_tag.py` | ChannelTag trigger source identifier |
| `a2a_schema.py` | Full A2A protocol v0.3 wire types |
| `api_schema.py` | HTTP request/response DTOs for all routes |
| `provider_schema.py` | Multi-provider LLM configuration (ProviderConfig, SlotConfig) |
| `skill_schema.py` | Installed Skill metadata |

## Collaboration with other directories

- `repository/` imports domain entity models from `entity_schema.py`, `instance_schema.py`, `job_schema.py`, `inbox_schema.py`, `agent_message_schema.py`, and `rag_store_schema.py` to do row-to-entity conversion.
- `agent_runtime/` and `context_runtime/` use `context_schema.py`, `hook_schema.py`, `decision_schema.py`, and `runtime_message.py` as their pipeline contracts.
- `backend/routes/` imports from `api_schema.py` for all request validation and response construction.
- `module/` uses `module_schema.py` and `instance_schema.py` for the module lifecycle.
- Nothing outside `schema/` should define its own Pydantic models that duplicate what is here. When you need a new data shape, add it here first.
