# Phase 2: Agent Message Bus — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Matrix/Synapse with a built-in MessageBus for agent-to-agent communication. Local mode uses SQLite-backed durable bus; cloud mode uses REST API to cloud backend.

**Architecture:** `MessageBusService` ABC with two implementations: `LocalMessageBus` (SQLite + cursor-based delivery) and `CloudMessageBus` (HTTP client stub). Delivery polling merged into ModulePoller. Pydantic schemas for BusMessage, BusChannel, AgentInfo.

**Tech Stack:** Python 3.13, aiosqlite (via SQLiteBackend from Phase 1), pytest, pytest-asyncio

**Depends on:** Phase 1 complete (SQLiteBackend, MessageBus table DDL)

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/xyz_agent_context/message_bus/__init__.py` | Package exports |
| `src/xyz_agent_context/message_bus/schemas.py` | Pydantic models: BusMessage, BusChannel, BusChannelMember, BusAgentInfo |
| `src/xyz_agent_context/message_bus/message_bus_service.py` | `MessageBusService` ABC |
| `src/xyz_agent_context/message_bus/local_bus.py` | `LocalMessageBus` — SQLite implementation |
| `src/xyz_agent_context/message_bus/cloud_bus.py` | `CloudMessageBus` — HTTP client stub (interface only, impl deferred to cloud deployment) |
| `tests/test_message_bus_schemas.py` | Schema validation tests |
| `tests/test_local_bus.py` | Full LocalMessageBus tests |

---

## Task 1: MessageBus Pydantic Schemas

**Files:**
- Create: `src/xyz_agent_context/message_bus/__init__.py`
- Create: `src/xyz_agent_context/message_bus/schemas.py`
- Create: `tests/test_message_bus_schemas.py`

Define data models that match the bus tables from Phase 1:

```python
class BusMessage(BaseModel):
    message_id: str
    channel_id: str
    from_agent: str
    content: str
    msg_type: str = "text"
    created_at: str  # ISO 8601

class BusChannel(BaseModel):
    channel_id: str
    name: str
    channel_type: str = "group"  # "direct" | "group"
    created_by: str
    created_at: str

class BusChannelMember(BaseModel):
    channel_id: str
    agent_id: str
    joined_at: str
    last_read_at: str
    last_processed_at: Optional[str] = None

class BusAgentInfo(BaseModel):
    agent_id: str
    owner_user_id: str
    capabilities: List[str] = []
    description: str = ""
    visibility: str = "private"  # "public" | "private"
    registered_at: str
    last_seen_at: str
```

Tests: verify model creation, serialization, defaults.

---

## Task 2: MessageBusService ABC

**Files:**
- Create: `src/xyz_agent_context/message_bus/message_bus_service.py`

Abstract interface with all methods from the design spec:

```python
class MessageBusService(ABC):
    # Messaging
    async def send_message(from_agent, to_channel, content, msg_type="text") -> str
    async def get_messages(channel_id, since=None, limit=50) -> List[BusMessage]
    async def get_unread(agent_id) -> List[BusMessage]
    async def mark_read(agent_id, message_ids) -> None

    # Channel Management
    async def create_channel(name, members, channel_type="group") -> str
    async def join_channel(agent_id, channel_id) -> None
    async def leave_channel(agent_id, channel_id) -> None

    # Agent Discovery
    async def register_agent(agent_id, owner_user_id, capabilities, description, visibility="private") -> None
    async def search_agents(query, limit=10) -> List[BusAgentInfo]

    # Delivery (for trigger/poller)
    async def get_pending_messages(agent_id, limit=50) -> List[BusMessage]
    async def ack_processed(agent_id, channel_id, up_to_timestamp) -> None
    async def record_failure(message_id, agent_id, error) -> None
    async def get_failure_count(message_id, agent_id) -> int
```

No tests needed — ABC is tested through implementations.

---

## Task 3: LocalMessageBus Implementation

**Files:**
- Create: `src/xyz_agent_context/message_bus/local_bus.py`
- Create: `tests/test_local_bus.py`

Implements `MessageBusService` using `SQLiteBackend` from Phase 1.

Key behaviors:
- `send_message`: generates `msg_XXXXXXXX` ID, inserts into bus_messages
- `create_channel`: generates `ch_XXXXXXXX` ID, inserts channel + members
- `get_unread`: query using `last_read_at` cursor (UI level)
- `get_pending_messages`: query using `last_processed_at` cursor (runtime level), skips self-sent, respects failure count >= 3
- `ack_processed`: updates `last_processed_at` on bus_channel_members
- `mark_read`: updates `last_read_at`
- `register_agent`: upserts into bus_agent_registry
- `search_agents`: simple LIKE query on capabilities/description (V1, no vector search yet)
- `record_failure` / `get_failure_count`: manage bus_message_failures table

Tests covering:
1. Send message and retrieve
2. Create channel with members
3. Unread messages (UI cursor)
4. Pending messages (runtime cursor) — the core delivery model
5. Ack processed advances cursor
6. Poison message skipped after 3 failures
7. Register and search agents
8. Join/leave channel
9. Mark read

---

## Task 4: CloudMessageBus Stub

**Files:**
- Create: `src/xyz_agent_context/message_bus/cloud_bus.py`

Minimal stub implementing `MessageBusService`. All methods raise `NotImplementedError("Cloud message bus requires cloud API endpoint")`. This is a placeholder — real implementation comes during cloud deployment.

One exception: the constructor should accept `api_base_url: str` and `auth_token: str` parameters to establish the pattern.

No tests needed for a stub.

---

## Task 5: Package Exports

**Files:**
- Update: `src/xyz_agent_context/message_bus/__init__.py`

Export public API:
```python
from .message_bus_service import MessageBusService
from .local_bus import LocalMessageBus
from .cloud_bus import CloudMessageBus
from .schemas import BusMessage, BusChannel, BusChannelMember, BusAgentInfo
```

Run all tests to verify nothing broke.
