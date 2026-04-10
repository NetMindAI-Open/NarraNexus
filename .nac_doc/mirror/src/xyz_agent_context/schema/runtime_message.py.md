---
code_file: src/xyz_agent_context/schema/runtime_message.py
last_verified: 2026-04-10
stub: false
---

# runtime_message.py

## Why it exists

`AgentRuntime` is an async generator — it `yield`s messages as execution progresses. Without a typed message hierarchy, the consumer (frontend WebSocket handler or Streamlit app) must parse raw dicts and guess what each payload means. This file defines the typed message classes that `AgentRuntime` yields, giving both producers and consumers a stable contract for streaming communication.

## Upstream / Downstream

`AgentRuntime` yields instances of `ProgressMessage`, `AgentTextDelta`, `AgentThinking`, `AgentToolCall`, and `ErrorMessage`. The WebSocket route in `backend/routes/` iterates the generator, calls `.to_dict()` on each message, and sends the JSON over the socket to the frontend. The React frontend's `useAgentChat` hook reconstructs the message stream and routes each `type` field to the appropriate UI component.

`HookExecutionTrace.agent_loop_response` also stores a list of these message objects (or SDK-native objects that mirror this shape) as the raw execution trace for post-hoc analysis by modules like `JobModule`.

## Design decisions

**`message_type` field with `serialization_alias="type"`**: the Python attribute is `message_type` (Pydantic convention) but it serializes as `"type"` in JSON (frontend API convention). This was a deliberate translation to match what the React components expect without renaming the Python attribute everywhere.

**`to_dict()` with `mode='json'`** ensures enums serialize as their string values rather than enum objects. The method also renames `message_type` to `type` in the output dict to enforce the frontend convention consistently.

**`ABC` base class**: `BaseRuntimeMessage` is abstract, preventing direct instantiation. This was added to make it clear that only the concrete subtypes should be yielded — you can `isinstance(msg, BaseRuntimeMessage)` to check if something is a runtime message without knowing which subtype.

**`AgentThinking` for transparency**: exposing the model's thinking process is optional in most SDKs. Yielding `AgentThinking` separately from `AgentTextDelta` lets the frontend collapse thinking into an expandable section without mixing it with the visible reply.

## Gotchas

**`ProgressMessage.step`** is a string like `"1.0"`, `"2.1"`, `"3"`. The step numbering follows the AgentRuntime pipeline steps (Steps 1-8). There is no validation that step values are unique or monotonically increasing within a single execution. Frontend code that tries to sort or group by step value must handle arbitrary string ordering.

**`ErrorMessage.error_type`** is a free-form string (`"api_error"` by default). There is no enum constraining its values. The frontend uses this for display styling and routing to error-specific handling, but if a new error type is introduced on the backend, the frontend may not have a matching handler.

## New-joiner traps

- `AgentTextDelta.delta` is a *chunk*, not the full response. Multiple deltas must be concatenated by the consumer to form the complete agent response. Do not display `delta` alone as if it were the complete answer.
- These message types are used for real-time streaming only. The persistent record of what the agent said is stored in `EventLogEntry` (in `narrative/models.py`), not in these objects. The `agent_loop_response` list in `HookExecutionTrace` may hold serialized versions of these objects for post-hoc analysis, but the source of truth for storage is always the event log.
