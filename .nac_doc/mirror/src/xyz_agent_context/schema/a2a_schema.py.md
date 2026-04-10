---
code_file: src/xyz_agent_context/schema/a2a_schema.py
last_verified: 2026-04-10
stub: false
---

# a2a_schema.py

## Why it exists

This file is a full implementation of Google's open A2A (Agent-to-Agent) protocol v0.3. It lives in schema/ because it is pure data contract — no business logic, no database coupling. The protocol structures here are what get serialized over the wire when NexusAgent either accepts calls from external agents or calls out to other A2A-compliant agents.

## Upstream / Downstream

Upstream producers that construct these objects are the A2A endpoint handlers in `backend/routes/` receiving inbound JSON-RPC calls, and the A2A client code in `agent_framework/` when NexusAgent calls a remote agent. Downstream consumers are the route handlers that deserialize `JSONRPCRequest`, dispatch by `method` field, and return `JSONRPCResponse`. The frontend never sees these types directly.

The `AgentCard` is special — it answers the `GET /.well-known/agent.json` endpoint and is the handshake entry point for any external agent discovering NexusAgent's capabilities.

## Design decisions

**Why JSON-RPC 2.0 and not REST?** The A2A spec mandates JSON-RPC 2.0 for all task operations. The choice was not NexusAgent's to make — the protocol is adopted wholesale so future A2A-compliant tools interoperate without custom adapters.

**Why `Part = Union[TextPart, FilePart, DataPart]` rather than a single generic payload?** Mirrors the spec exactly. Adding a new part type in the future is additive (new union member) and does not break existing deserializers that handle only TextPart. A flat dict approach was rejected because it loses type safety on the Python side.

**`AgentSkill` vs `ModuleConfig`** look similar but serve different audiences. `ModuleConfig` describes internal hot-pluggable modules to the Python runtime. `AgentSkill` describes capabilities to external agents in a protocol-standard vocabulary. They are intentionally not unified.

## Gotchas

**`TaskState` value contains a hyphen**: `INPUT_REQUIRED = "input-required"`. When you serialize and round-trip through JSON you get the hyphenated form, which is correct for the wire. If you compare against `"input_required"` (underscore) the comparison silently fails — no exception, the state just never matches.

**`Task.contextId` auto-generates a fresh UUID** even when you do not supply one. Two `Task()` instances created in sequence will have different `contextId` values. If you intend to continue an existing conversation thread you must explicitly pass the `contextId` from the previous task, otherwise the remote agent treats each request as a brand-new conversation.

**`JSONRPCResponse.error()` is a classmethod named `error`** which shadows the instance field `error: Optional[JSONRPCError]`. If you call `response.error(...)` on an instance you will get `TypeError: 'JSONRPCError' object is not callable`. Always call it as `JSONRPCResponse.error(...)` on the class.

## New-joiner traps

- The `Message` model here is completely unrelated to `AgentMessage` in `agent_message_schema.py` and `InboxMessage` in `inbox_schema.py`. They share no inheritance. `Message` here is A2A protocol vocabulary; the others are internal persistence models.
- `TaskSendConfiguration.blocking=False` by default. For `tasks/sendSubscribe` (streaming) this field is ignored entirely.
- `A2AErrorCodes` is a plain class with integer class attributes, not an Enum. You cannot iterate over it.
