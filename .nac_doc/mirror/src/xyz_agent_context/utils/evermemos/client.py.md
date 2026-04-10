# evermemos/client.py

HTTP client for the optional EverMemOS external memory service — writes Events and retrieves memories aggregated by Narrative.

## Why it exists

EverMemOS is an optional external memory backend that can supplement or replace the project's built-in Narrative memory system. When the `EVERMEMOS_BASE_URL` environment variable is set, the system can write events to EverMemOS (via `POST /api/v1/memories`) and retrieve relevant memories for a query (via `GET /api/v1/memories/search`). `client.py` provides the HTTP client that performs these operations without polluting the Narrative or Module layers with HTTP concerns. It was migrated here from `narrative/_narrative_impl/evermemos_service.py` to keep it as a utility separate from narrative orchestration logic.

## Upstream / Downstream

**Instantiated by:** code that needs EverMemOS integration (previously `narrative/_narrative_impl/`, now accessible to any module). The `get_evermemos_client(agent_id, user_id)` factory function returns a cached instance per `(agent_id, user_id)` pair.

**Calls:** `EVERMEMOS_BASE_URL/api/v1/memories` (write) and `EVERMEMOS_BASE_URL/api/v1/memories/search` (retrieve). The base URL defaults to `http://localhost:1995`.

**Depends on:** `httpx` for HTTP, `narrative/models.py` (for `Event`, `Narrative`, `NarrativeSearchResult` — type-checked only, via `TYPE_CHECKING`).

## Design decisions

**Per-(agent_id, user_id) client cache.** Each unique combination of agent and user gets one `EverMemOSClient` instance cached in `_evermemos_clients`. This avoids creating a new `httpx.AsyncClient` on every call while still providing isolation between different agents or users.

**`TYPE_CHECKING` guard for Narrative models.** The client imports `Event`, `Narrative`, and `NarrativeSearchResult` only under `TYPE_CHECKING` to avoid a circular import (narrative models depend on the database layer which is in `utils/`). At runtime these types are referenced only in type annotations, so no actual import happens.

**Two-step write: conversation-meta then event.** Before writing the first event for a Narrative, the client calls `POST /api/v1/memories/conversation-meta` to register the Narrative with EverMemOS. The result is cached in `_conversation_meta_saved` so subsequent events for the same Narrative skip this step.

**`EVERMEMOS_TIMEOUT` is configurable.** Calls to EverMemOS can be slow if the service is under load. The timeout defaults to 30 seconds but can be overridden via the environment variable.

## Gotchas

**EverMemOS is optional — failures should not block the main agent flow.** Callers must catch exceptions from `write_event` and `search_memories` and treat them as non-fatal. A slow or unavailable EverMemOS service should degrade gracefully rather than failing the agent turn.

**The global `_evermemos_clients` cache grows unbounded.** In a long-running process with many distinct `(agent_id, user_id)` pairs, this dict accumulates entries indefinitely. For a multi-tenant production deployment, a bounded cache (e.g., LRU) would be more appropriate.

**New-contributor trap.** `EverMemOSClient` uses `httpx.AsyncClient` internally but does not use a context manager (`async with`). The client is created once and reused. If the underlying HTTP connection becomes stale (e.g., the EverMemOS server restarts), `httpx` will reconnect automatically on the next request, so this is fine — but it means you cannot use `async with EverMemOSClient(...)` as a context manager.
