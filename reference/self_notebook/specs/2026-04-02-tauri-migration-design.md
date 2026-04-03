# NarraNexus Desktop App Migration Design

**Date:** 2026-04-02
**Branch:** `design/tauri-migration`
**Status:** Ready for review

## Goal

Transform NarraNexus from a developer-oriented Electron launcher (requiring Docker, Python, Node.js, etc.) into a **single DMG** that any non-technical user can install and use immediately. Support three runtime modes: local standalone, cloud app, and cloud web.

## Current Pain Points

| Problem | Cause |
|---------|-------|
| Final UI opens in external browser | Electron shell is only a launcher; the real chat UI is a separate Vite app at `localhost:8000` |
| Installation requires 15-30 minutes of setup | Preflight requires: Docker Desktop, uv, Python >= 3.13, Node.js >= 20, Claude Code CLI |
| Local bootstrap is still infrastructure-heavy | Core local startup depends on Docker for MySQL + Synapse; optional memory infrastructure increases complexity further |
| ~2GB+ of dependencies | Docker Desktop alone is 2GB+, before counting Python/Node toolchain setup |

## Target User Experience

```
Download NarraNexus.dmg (~180-250MB)
  -> Drag to Applications
  -> Double-click to open (no Gatekeeper warning, app is notarized)
  -> Choose: Local Mode / Cloud Mode
  -> Local: auto-init in ~10 seconds, configure API key, start chatting
  -> Cloud: log in, start chatting immediately
```

---

## Architecture Overview

```
+---------------------------------------------------------------+
|                    NarraNexus App (Tauri 2)                    |
|  +----------------------------------------------------------+ |
|  |              React Frontend (unified)                     | |
|  |  +------+ +----------+ +------+ +--------+ +------+      | |
|  |  | Chat | |Awareness | | Jobs | |Settings| |System|      | |
|  |  +--+---+ +----+-----+ +--+---+ +---+----+ +--+---+      | |
|  |     +----------+---------+---------+----------+           | |
|  |                        |                                  | |
|  |                 PlatformBridge                            | |
|  |              +----------+-----------+                     | |
|  |         TauriBridge            WebBridge                  | |
|  +----------+-------------------------+---------------------+ |
|             |                         |                       |
|     +-------v--------+      +--------v--------+              |
|     |  Tauri Rust     |      |  (Web mode:     |              |
|     |  - Process mgmt |      |   no shell,     |              |
|     |  - Health check |      |   direct cloud  |              |
|     |  - System tray  |      |   API)          |              |
|     |  - Auto-update  |      +--------+--------+              |
|     +-------+---------+              |                        |
+--------------+------------------------+-----------------------+
               |
    +----------v-----------------------------------------+
    |            Python Backend (unified)                 |
    |  +----------------------------------------------+  |
    |  |         AsyncDatabaseClient                   |  |
    |  |     +----------+  +-----------+              |  |
    |  |     |SQLite    |  | MySQL     |              |  |
    |  |     |(local)   |  | (cloud)   |              |  |
    |  |     +----------+  +-----------+              |  |
    |  +----------------------------------------------+  |
    |  |         MessageBusService                     |  |
    |  |     +----------+  +-----------+              |  |
    |  |     |LocalBus  |  | CloudBus   |              |  |
    |  |     |(local)   |  | (cloud)    |              |  |
    |  |     +----------+  +-----------+              |  |
    |  +----------------------------------------------+  |
    |  |         AgentExecutor                         |  |
    |  |     +----------+  +-----------+              |  |
    |  |     |ClaudeCode|  | API Mode   |              |  |
    |  |     |(local +  |  |(external  |              |  |
    |  |     | internal)|  | users)    |              |  |
    |  |     +----------+  +-----------+              |  |
    |  +----------------------------------------------+  |
    +----------------------------------------------------+
```

---

## Runtime Mode Matrix

| Dimension | Local Mode | Cloud App Mode | Cloud Web Mode |
|-----------|-----------|---------------|---------------|
| **Shell** | Tauri (manages processes) | Tauri (pure shell) | Browser |
| **Frontend** | Same React app | Same React app | Same React app |
| **API URL** | `localhost:8000` | `api.narranexus.com` | `api.narranexus.com` |
| **Backend** | Local Python sidecar | Cloud server | Cloud server |
| **Database** | SQLite | AWS RDS (MySQL) | AWS RDS (MySQL) |
| **Message Bus** | LocalMessageBus | CloudMessageBus | CloudMessageBus |
| **Long-term Memory** | Native vector / event-memory only (`EverMemOS` off) | EverMemOS + native fallback | EverMemOS + native fallback |
| **Cross-user comms** | No (single user on one device) | Yes | Yes |
| **Auth Model** | Local profile / local session only | Server session / JWT required | Server session / JWT required |
| **Claude Code** | User's own (anyone) | Internal employees only | Internal employees only |
| **API Mode** | Optional | All users | All users |
| **System page** | Visible | Hidden | Hidden |
| **Auto-update** | Tauri updater | Tauri updater | N/A |
| **Offline** | Yes | No | No |

---

## Prerequisite Decisions

These decisions must be treated as locked assumptions for the migration:

1. **Local mode does not start EverMemOS.**
   - Local mode uses SQLite + native vector retrieval / event memory only.
   - `EVERMEMOS_ENABLED=false` in local packaged desktop builds.
   - EverMemOS remains a cloud-only capability.

2. **V1 keeps the existing multi-process runtime model.**
   - Local desktop still launches multiple Python processes (`backend`, `mcp`, `poller`, `job-trigger`).
   - Therefore, local message delivery cannot rely on in-memory callbacks as the correctness mechanism.

3. **Cloud mode requires real authentication and authorization.**
   - Passing `user_id` in request body or query string is acceptable only for current local/dev flows.
   - Cloud App and Cloud Web require server-validated session/JWT, permission checks, rate limits, and audit logs.

4. **"Zero upper-layer changes" is not a design constraint.**
   - The DB backend abstraction should preserve the broad repository API where possible.
   - Repositories, raw SQL routes, and table-management scripts are allowed to change where MySQL-specific behavior leaks through.

---

## Phase 1: MySQL to SQLite (Pluggable Database Backend)

### 1.1 Strategy

Create a pluggable database backend behind the existing `AsyncDatabaseClient` interface, but do **not** treat "upper layers require zero changes" as a hard requirement. The real goal is:

- Keep the high-level repository/service shape stable where practical
- Isolate dialect differences behind backend adapters when possible
- Explicitly refactor repositories and raw SQL call sites that currently depend on MySQL-only behavior
- Make local packaged desktop run without MySQL present at all

```
AsyncDatabaseClient (unified interface, unchanged)
    +-- SQLiteBackend  (local mode default)   <- new
    +-- MySQLBackend   (cloud mode)           <- extracted from current code
```

Configuration switch:

```python
# Local mode (zero config)
DATABASE_URL="sqlite:///~/Library/Application Support/NarraNexus/nexus.db"

# Cloud mode (AWS RDS)
DATABASE_URL="mysql://user:pass@rds-endpoint:3306/nexus"
```

Migration rule:

1. `AsyncDatabaseClient` stays as the public facade used by repositories and services.
2. Table-management utilities get a dialect-aware DDL/introspection layer instead of assuming MySQL metadata APIs.
3. Raw SQL in repositories/routes/modules is audited and either:
   - rewritten into backend-neutral patterns, or
   - isolated behind cloud-only code paths.

Definition of done for Phase 1:

- Local packaged desktop boots with SQLite and no MySQL service
- Cloud server still boots with MySQL
- Local mode passes schema creation and repository smoke tests without MySQL installed

### 1.2 MySQL-Specific Features Migration

| MySQL Feature | SQLite Equivalent | Migration Effort |
|---------------|-------------------|-----------------|
| `INSERT ... ON DUPLICATE KEY UPDATE` | SQLite 3.24+ `UPSERT` (`ON CONFLICT ... DO UPDATE`) | Medium |
| `ENUM` type | `TEXT` + `CHECK` constraint | Low |
| `DATETIME(6)` microsecond precision | `TEXT` (ISO 8601 format) | Low |
| `ON UPDATE CURRENT_TIMESTAMP` | Python-side assignment in `update()` method | Low |
| `MEDIUMTEXT` | `TEXT` (no size limit in SQLite) | Direct replace |
| `BIGINT UNSIGNED AUTO_INCREMENT` | `INTEGER PRIMARY KEY AUTOINCREMENT` | DDL change |
| `%s` placeholder | `?` placeholder | Backend-internal conversion |
| `utf8mb4` charset | Default UTF-8 | Remove |
| `JSON_CONTAINS()` / `JSON_EXTRACT()` | SQLite JSON1 extension or `narrative_participants` join table | Medium |

### 1.3 SQLite Performance Configuration

```sql
PRAGMA journal_mode = WAL;          -- concurrent reads + writes
PRAGMA synchronous = NORMAL;        -- safe in WAL, 2-3x faster than FULL
PRAGMA cache_size = -64000;         -- 64MB page cache (default only 2MB)
PRAGMA mmap_size = 268435456;       -- 256MB memory-mapped I/O
PRAGMA temp_store = MEMORY;         -- temp tables in memory
PRAGMA busy_timeout = 5000;         -- wait 5s on write contention
PRAGMA foreign_keys = ON;           -- enforce FK constraints
```

### 1.4 Connection Management

```python
class SQLiteBackend:
    _write_lock: asyncio.Lock          # serialize writes
    _connection: aiosqlite.Connection   # long-lived, reused

    async def _ensure_connection(self):
        if self._connection is None:
            self._connection = await aiosqlite.connect(self._db_path)
            self._connection.row_factory = aiosqlite.Row
            await self._apply_pragmas()

    async def execute_write(self, sql, params):
        async with self._write_lock:
            await self._connection.execute(sql, params)
            await self._connection.commit()

    async def execute_read(self, sql, params):
        # No lock needed -- WAL allows concurrent reads
        return await self._connection.execute(sql, params)
```

Single long-lived connection + WAL is optimal for SQLite. Unlike MySQL, multiple connections do not improve performance and only increase lock contention.

### 1.5 Query Performance Optimizations

**Optimization 1: Push JSON filtering into SQL**

Current (Python-side filtering, wastes I/O):
```python
rows = await db.get("narratives", {"agent_id": agent_id}, limit=20)
results = [r for r in rows if user_id in r.narrative_info.actors]
```

Optimized (add join table to avoid JSON parsing):
```sql
CREATE TABLE narrative_participants (
    narrative_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    participant_type TEXT NOT NULL,
    PRIMARY KEY (narrative_id, participant_id)
);
-- Query becomes a simple JOIN, 10x+ faster than JSON parsing
```

**Optimization 2: Lazy-load large Event fields**

Current: `SELECT *` loads `env_context`, `module_instances` (large JSON) even when only metadata is needed.

```sql
-- List query: lightweight fields only
SELECT event_id, narrative_id, event_type, created_at
FROM events WHERE narrative_id = ? ORDER BY created_at DESC LIMIT 100;

-- Detail query: load full data on demand
SELECT * FROM events WHERE event_id = ?;
```

**Optimization 3: True batch upsert for embeddings**

Current: loop of individual INSERTs (N+1 pattern).

```python
async def upsert_batch(self, entities: List[EmbeddingRecord]):
    sql = """INSERT INTO embeddings_store (entity_type, entity_id, model, dimensions, vector, source_text)
             VALUES (?, ?, ?, ?, ?, ?)
             ON CONFLICT(entity_type, entity_id) DO UPDATE SET
               vector = excluded.vector,
               source_text = excluded.source_text,
               updated_at = datetime('now')"""
    params = [(e.entity_type, e.entity_id, e.model, e.dimensions,
               json.dumps(e.vector), e.source_text) for e in entities]
    async with self._write_lock:
        await self._connection.executemany(sql, params)
        await self._connection.commit()
```

**Optimization 4: Index strategy**

```sql
CREATE INDEX idx_narratives_agent_updated ON narratives(agent_id, updated_at DESC);
CREATE INDEX idx_events_narrative_created ON events(narrative_id, created_at DESC);
CREATE INDEX idx_instances_agent_status ON module_instances(agent_id, status);
CREATE INDEX idx_instances_poll ON module_instances(status, last_polled_status, callback_processed);
CREATE INDEX idx_embeddings_entity ON embeddings_store(entity_type, entity_id);
CREATE INDEX idx_bus_msg_channel_time ON bus_messages(channel_id, created_at);
CREATE INDEX idx_bus_member_agent ON bus_channel_members(agent_id);
```

### 1.6 Database File Location

```
macOS: ~/Library/Application Support/NarraNexus/nexus.db
Linux: ~/.local/share/NarraNexus/nexus.db
```

Tauri's `app_data_dir()` handles this automatically.

### 1.7 Dependency Changes

- `aiomysql` becomes optional: `pip install narranexus[cloud]`
- `aiosqlite` added as core dependency
- `numpy` retained (vector similarity computation)

### 1.8 Files Affected

| File | Change |
|------|--------|
| `utils/database.py` | Refactor into pluggable backend facade |
| `utils/db_backend_sqlite.py` | New: SQLite backend implementation |
| `utils/db_backend_mysql.py` | New: extracted from current database.py |
| `utils/db_factory.py` | Select backend by DATABASE_URL scheme |
| `repository/base.py` | Adapt UPSERT syntax (backend handles internally) |
| `repository/narrative_repository.py` | Add narrative_participants table, remove JSON_CONTAINS |
| `repository/embedding_store_repository.py` | True batch upsert |
| raw SQL routes / repositories | Audit `%s`, `DATE_SUB`, `JSON_EXTRACT`, `information_schema`, `SHOW COLUMNS`, etc. |
| 18x `create_*_table.py` | Dual-dialect DDL (SQLite + MySQL) |
| 18x `modify_*_table.py` | SQLite: recreate-table pattern (ALTER limitations) |

---

## Phase 2: Agent Message Bus (Replaces Matrix/Synapse)

### 2.1 What Matrix Currently Does

| Capability | Matrix Implementation |
|------------|----------------------|
| Agent-to-agent messaging | `matrix_send_message(room_id, content)` |
| Create channels | `matrix_create_room(name, members)` |
| Discover agents by capability | `matrix_search_agents(query)` (semantic search) |
| Inbox | `matrix_get_inbox()` (fetch unread) |
| Register agent identity | `matrix_register(agent_id)` |
| Background polling | MatrixTrigger polls every 15-120 seconds |

Core need: **message send/receive + channel management + agent discovery**. Matrix protocol is overkill.

### 2.2 Pluggable Message Bus

```
MessageBusService (abstract interface)
    +-- LocalMessageBus  (local mode)   <- SQLite durable bus
    +-- CloudMessageBus  (cloud mode)   <- REST / WebSocket to cloud backend
```

### 2.3 Unified Interface

```python
class MessageBusService(ABC):
    """Agent communication service -- replaces Matrix/Synapse"""

    # --- Messaging ---
    async def send_message(self, from_agent: str, to_channel: str, content: str,
                           msg_type: str = "text") -> str:  # returns message_id

    async def get_messages(self, channel_id: str, since: datetime | None = None,
                           limit: int = 50) -> List[BusMessage]:

    async def get_unread(self, agent_id: str) -> List[BusMessage]:

    async def mark_read(self, agent_id: str, message_ids: List[str]) -> None

    # --- Channel Management ---
    async def create_channel(self, name: str, members: List[str],
                             channel_type: str = "group") -> str:

    async def join_channel(self, agent_id: str, channel_id: str) -> None

    async def leave_channel(self, agent_id: str, channel_id: str) -> None

    # --- Agent Discovery ---
    async def register_agent(self, agent_id: str, capabilities: List[str],
                             description: str) -> None

    async def search_agents(self, query: str, limit: int = 10) -> List[AgentInfo]:

    # --- Real-time Subscriptions ---
    async def subscribe(self, agent_id: str, channel_id: str,
                        callback: Callable[[BusMessage], Awaitable[None]]) -> str:

    async def unsubscribe(self, token: str) -> None
```

### 2.4 LocalMessageBus Implementation

**Database tables:**

```sql
CREATE TABLE bus_channels (
    channel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel_type TEXT NOT NULL DEFAULT 'group',  -- 'direct' | 'group'
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE bus_channel_members (
    channel_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_read_at TEXT NOT NULL DEFAULT (datetime('now')),       -- UI-level "read" cursor
    last_processed_at TEXT,                                     -- runtime-level "processed" cursor
    PRIMARY KEY (channel_id, agent_id)
);

CREATE TABLE bus_message_failures (
    message_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_retry_at TEXT,
    PRIMARY KEY (message_id, agent_id)
);

CREATE TABLE bus_messages (
    message_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    content TEXT NOT NULL,
    msg_type TEXT NOT NULL DEFAULT 'text',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE bus_agent_registry (
    agent_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    capabilities TEXT,              -- JSON array
    description TEXT,
    capability_embedding TEXT,      -- JSON array of floats
    visibility TEXT NOT NULL DEFAULT 'private',  -- 'public' | 'private'
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE bus_delivery_cursors (
    consumer_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    last_seen_message_id TEXT,
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (consumer_id, channel_id)
);
```

**Delivery model (correct for multi-process local runtime):**

```python
class LocalMessageBus(MessageBusService):
    async def send_message(self, from_agent, to_channel, content, msg_type="text"):
        message_id = generate_id("msg")
        await self._db.insert("bus_messages", {
            "message_id": message_id,
            "channel_id": to_channel,
            "from_agent": from_agent,
            "content": content,
            "msg_type": msg_type,
        })
        return message_id
```

Process-boundary rules:

- **Backend -> frontend/WebView:** backend process may keep in-memory subscriptions and push updates through WebSocket/SSE.
- **Backend -> worker processes / agent triggers:** use durable DB-backed cursors (`bus_delivery_cursors`) plus a lightweight `MessageBusTrigger` scan, or fold the scan into `ModulePoller`.
- **Do not rely on in-memory callback state for cross-process correctness.**

V1 target:

- Replace Synapse/NexusMatrix with a much simpler durable bus
- Keep local delivery latency in the low-seconds range
- Preserve correctness across separate Python processes

Future optimization:

- If the runtime is later collapsed into fewer processes, add an in-process callback fast path on top of the same durable bus tables.

**Unread query (optimized):**

```sql
SELECT m.* FROM bus_messages m
JOIN bus_channel_members cm ON m.channel_id = cm.channel_id
WHERE cm.agent_id = ?
  AND m.created_at > cm.last_read_at
ORDER BY m.created_at ASC;
```

**Agent discovery:** Reuses existing VectorStore + numpy cosine similarity on `capability_embedding`.

### 2.5 Delivery Semantics

**Guarantee:** at-least-once delivery. Messages are never silently dropped.

**Trigger ownership:** MessageBus polling is merged into ModulePoller (no separate process).
ModulePoller already polls every 5 seconds; adding message checks is near-zero cost.

**Cursor model:** Each agent tracks its own processing position via `bus_channel_members.last_processed_at` (separate from UI-level `last_read_at`).

**Polling query (unified for local and cloud):**

```sql
SELECT m.* FROM bus_messages m
JOIN bus_channel_members cm ON m.channel_id = cm.channel_id
WHERE cm.agent_id = ?
  AND m.created_at > COALESCE(cm.last_processed_at, '1970-01-01')
  AND m.from_agent != ?   -- skip self-sent messages
ORDER BY m.created_at ASC
LIMIT 50;
```

**Cursor advancement (after successful processing):**

```sql
UPDATE bus_channel_members
SET last_processed_at = ?   -- created_at of last successfully processed message
WHERE channel_id = ? AND agent_id = ?;
```

**Crash recovery:** Cursor only advances after successful processing. On crash and restart, unprocessed messages are automatically replayed. Agent-side idempotency via `message_id`.

**Poison message handling:**
1. Processing failure increments `retry_count` in `bus_message_failures`
2. `retry_count >= 3` → message is skipped, cursor advances past it
3. Failed messages are logged but do not block the queue

**Local vs cloud execution:**

| | Local Mode | Cloud Mode |
|--|-----------|------------|
| Trigger | ModulePoller (single process) | MessageBusWorker (cloud service, horizontally scalable) |
| Database | SQLite | MySQL |
| Sharding | Not needed (single user) | By `agent_id` hash across worker instances |
| Logic | Identical SQL and cursor model | Identical SQL and cursor model |

**Observability:**

```sql
-- Queue depth per agent
SELECT COUNT(*) FROM bus_messages m
JOIN bus_channel_members cm ON m.channel_id = cm.channel_id
WHERE cm.agent_id = ?
  AND m.created_at > COALESCE(cm.last_processed_at, '1970-01-01');

-- Max delivery lag in seconds
SELECT MAX(julianday('now') - julianday(m.created_at)) * 86400 AS lag_seconds
FROM bus_messages m
JOIN bus_channel_members cm ON m.channel_id = cm.channel_id
WHERE cm.agent_id = ?
  AND m.created_at > COALESCE(cm.last_processed_at, '1970-01-01');
```

Queue depth and lag are exposed on the frontend System page (local mode) and admin dashboard (cloud mode).

### 2.6 CloudMessageBus Implementation

```python
class CloudMessageBus(MessageBusService):
    """Cloud mode: all calls become HTTP requests to cloud API"""

    def __init__(self, api_base_url: str, auth_token: str):
        self._client = httpx.AsyncClient(base_url=api_base_url)
        self._auth_token = auth_token

    async def send_message(self, from_agent, to_channel, content, msg_type="text"):
        resp = await self._client.post("/api/bus/messages", json={...})
        return resp.json()["message_id"]

    async def subscribe(self, agent_id, channel_id, callback):
        # Cloud mode: WebSocket or SSE for real-time messages
        # Connect to wss://api.xxx.com/ws/bus/{agent_id}
        ...
```

The cloud API server runs the same `LocalMessageBus` logic internally, just backed by MySQL instead of SQLite.

### 2.7 Cross-User Communication (Cloud Mode)

In cloud mode, public agents can be discovered and messaged by any user.

**Permission model:**

| | Local Mode | Cloud Mode |
|--|-----------|------------|
| Communication scope | Same user's agents only | All users' agents |
| Agent discovery | Only own agents | All `visibility = 'public'` agents + own agents |
| Channel visibility | All visible (single user) | `private` (invite only) / `public` (searchable) |
| Permission control | Minimal | Server-enforced authz + agent visibility metadata |

### 2.8 Public Agent Interaction Model

Agents marked `is_public = true` can be interacted with by any user in cloud mode.

**Security boundary:** the backend enforces authentication and authorization. Agent reasoning is useful for behavior, but is **not** the primary security control.

Server responsibilities:

- authenticate the caller (session / JWT)
- authorize visibility to public/private agents and channels
- enforce creator-only operations server-side
- apply rate limits / abuse controls
- write audit logs for public interactions

Agent context injection is still useful, but only as a behavioral hint:

Example context injection:
```
"This message is from external user [user_name] (user_id: [id]).
They are NOT your creator. Apply your public interaction policy."
```

The agent decides autonomously whether to:
- Serve the request normally
- Restrict sensitive information
- Decline certain operations
- Ask for authorization

This aligns with the Awareness-driven design philosophy of the project, but the **server remains the hard security boundary**.

### 2.9 Cloud Authentication and Authorization

**Identity provider:** Supabase Auth (managed). 50,000 MAU free tier. Open-source, can self-host later if needed.

**Login methods:**
- Google / GitHub social login (lowest friction for end users)
- Email + password registration

**Architecture:**

```
Frontend (React)
    |
    +-- @supabase/supabase-js SDK
    |   +-- Google / GitHub social login
    |   +-- Email + password registration
    |
    +-- Receives JWT access_token
    |
    +-- All API requests: Authorization: Bearer <token>
            |
            v
Python Backend (FastAPI)
    |
    +-- Middleware verifies JWT signature (Supabase public key, no remote call)
    +-- Extracts user_id, email, user_type from JWT claims
    +-- Injects into request context
```

**User type distinction:**

Supabase stores custom fields in `user.app_metadata` (server-set, user cannot modify):

```json
{
  "user_type": "internal",
  "org": "netmind"
}
```

- Internal employees: registered via admin invite, server sets `user_type: "internal"`
- External users: self-registration, default `user_type: "external"`

JWT automatically includes these claims. Backend parses directly, no extra DB lookup.

**Token lifecycle:**

| Concern | Solution |
|---------|----------|
| Storage | Tauri App: Tauri secure storage (OS keychain). Web: httpOnly cookie |
| Refresh | Supabase SDK auto-refresh (access_token expires in 1h, refresh_token auto-renews) |
| Logout | `supabase.auth.signOut()`, clears local tokens |
| Multi-device | Supabase native support, independent sessions per device |
| Revocation | Admin dashboard can revoke all sessions for a user |

**Backend auth middleware:**

```python
async def auth_middleware(request: Request, call_next):
    # Local mode: skip authentication entirely
    if app_config.mode == "local":
        request.state.user = LocalUser(
            user_id=app_config.local_user_id,
            user_type="internal"
        )
        return await call_next(request)

    # Cloud mode: verify JWT
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    payload = verify_supabase_jwt(token)  # local signature check, no remote call
    request.state.user = CloudUser(
        user_id=payload["sub"],
        email=payload["email"],
        user_type=payload["app_metadata"]["user_type"],
    )
    return await call_next(request)
```

**API authorization rules:**

```python
def require_agent_access(func):
    async def wrapper(request: Request, agent_id: str, ...):
        user = request.state.user
        agent = await agent_repo.get_by_id(agent_id)

        if agent.owner_user_id == user.user_id:
            pass  # creator: full access
        elif agent.is_public:
            pass  # public agent: allowed, agent decides boundaries
        else:
            return JSONResponse(status_code=403, content={"error": "Forbidden"})

        return await func(request, agent_id, ...)
    return wrapper
```

**Audit and rate limiting (V1 minimum):**

| Need | Solution |
|------|----------|
| Audit log | API middleware logs `user_id + action + agent_id + timestamp` to audit table |
| Rate limiting | FastAPI `slowapi`, keyed by `user_id`. External users get stricter limits |
| Abuse detection | V1: not implemented, rate limiting is the backstop |

### 2.10 MCP Tool Mapping

| Original Matrix Tool | New MessageBus Method | Change |
|---------------------|----------------------|--------|
| `matrix_send_message` | `bus.send_message()` | Simplified params |
| `matrix_create_room` | `bus.create_channel()` | room -> channel |
| `matrix_search_agents` | `bus.search_agents()` | Interface unchanged |
| `matrix_get_inbox` | `bus.get_unread()` | Interface unchanged |
| `matrix_register` | `bus.register_agent()` | Interface unchanged |

Module rename: `MatrixModule` -> `MessageBusModule`.

### 2.11 What Gets Removed

| Removed | Reason |
|---------|--------|
| Synapse service in `docker-compose.yaml` | No longer needed |
| `related_project/NetMind-AI-RS-NexusMatrix/` | No longer needed |
| `matrix_credentials` table | Replaced by `bus_agent_registry` |
| `matrix_processed_events` table | Replaced by bus delivery cursor state |
| MatrixTrigger background process | Replaced by `MessageBusTrigger` or ModulePoller-integrated bus scan |
| `matrix-nio` dependency | No longer needed |

**Net effect:** one fewer Docker container, one fewer external service, one fewer protocol dependency, and a much simpler local/cloud messaging stack. A lightweight trigger may still exist in V1 because the local runtime remains multi-process.

---

## Phase 3: Frontend Unification

### 3.1 Current State

Two independent React applications:

| | `frontend/` (main app) | `desktop/src/renderer/` (Dashboard) |
|--|----------------------|-------------------------------------|
| Framework | React 19 + React Router 7 + Vite | React 19 + electron-vite |
| Styling | Tailwind CSS 4 | Tailwind CSS 3 |
| State | Zustand + React Query | Native useState/useEffect |
| Features | Agent chat, Awareness, Jobs | Service start/stop, logs, Setup Wizard |
| Components | ~30+ | ~10 |

### 3.2 Merge Strategy

Dashboard components migrate into `frontend/` as a new route page. Tailwind unified to v4.

Components to migrate:

| Component | Purpose | Effort |
|-----------|---------|--------|
| `ServiceCard.tsx` | Service status card | Low -- pure display |
| `LogViewer.tsx` | Log viewer | Low -- pure display |
| `SetupWizard.tsx` + 3 sub-pages | First-run install guide | Medium -- needs Tauri IPC |
| `UpdateBanner.tsx` | Update notification | Low -- pure display |
| `StepIndicator.tsx` | Progress indicator | Low -- pure display |
| `Dashboard.tsx` | Service management page | Medium -- needs data source adapter |

### 3.3 Unified Route Structure

```
/                           -> root redirect based on mode
+-- /setup                  -> SetupWizard (local mode, first launch only)
+-- /login                  -> Login (cloud: account login; local: user select)
+-- /mode-select            -> First launch: local / cloud mode selection
+-- /app                    -> Main layout (sidebar + content area)
    +-- /app/chat           -> Agent chat (default)
    +-- /app/awareness      -> Awareness panel
    +-- /app/jobs           -> Job management
    +-- /app/settings       -> Model config + execution mode
    +-- /app/system         -> System management (original Dashboard)  <- new
        +-- Service status cards
        +-- Log viewer
        +-- Start/stop controls
```

### 3.4 Mode Detection, Runtime Config, and Feature Gating

```typescript
type AppMode = 'local' | 'cloud-app' | 'cloud-web'

type RuntimeConfig = {
  mode: AppMode
  apiBaseUrl: string
  authMode: 'local-dev' | 'session'
  userType?: 'internal' | 'external'
  featureFlags: string[]
}

async function loadRuntimeConfig(): Promise<RuntimeConfig> {
  if ((window as any).__TAURI__) {
    return await invoke<RuntimeConfig>('get_app_config')
  }
  const resp = await fetch('/app-config.json', { cache: 'no-store' })
  return await resp.json()
}
```

Rules:

- The frontend loads `RuntimeConfig` before mounting protected routes.
- `ApiClient` reads `apiBaseUrl` from runtime config, not only from build-time `VITE_API_BASE_URL`.
- Cloud feature gates come from authenticated server claims, not client-only flags.

UI differences by mode:

| Feature | Local | Cloud App | Cloud Web |
|---------|-------|-----------|-----------|
| `/app/system` (service mgmt) | Visible | Hidden | Hidden |
| `/setup` (install wizard) | First launch | Not needed | Not needed |
| `/mode-select` | First launch | Already selected | Not needed |
| Auto-update banner | Visible | Visible | Not needed |
| Claude Code status | Visible | Hidden | Hidden |
| Sidebar items | All | No system page | No system page |

### 3.5 Model Configuration and User Types

**Settings page (`/app/settings`):**

Users can configure their LLM provider:
- Provider selection (Anthropic, OpenAI, Google, custom)
- Base URL
- API Key
- Model selection

**User type determines execution mode visibility:**

```typescript
type UserType = 'internal' | 'external'

const FEATURE_GATES: Record<UserType, FeatureFlags> = {
  internal: {
    canUseClaudeCode: true,
    canUseApiMode: true,
    executionModes: ['claude-code', 'api'],
  },
  external: {
    canUseClaudeCode: false,
    canUseApiMode: true,
    executionModes: ['api'],
  }
}
```

Source of truth:

- Local mode: desktop config may supply local feature flags
- Cloud modes: authenticated backend session supplies `userType` and enabled capabilities

| | Local Mode | Cloud (Internal) | Cloud (External) |
|--|-----------|-----------------|-----------------|
| Claude Code | User's own (anyone can use) | Server-deployed (our account) | Not available |
| API Mode | Optional | Available | Only option |
| Settings UI | Both options shown | Both options shown | API config only |

### 3.6 Platform Abstraction Layer

```typescript
// platform.ts
interface PlatformBridge {
  // Service management (local mode only)
  getServiceStatus(): Promise<ProcessInfo[]>
  startAllServices(): Promise<void>
  stopAllServices(): Promise<void>
  restartService(id: string): Promise<void>
  getLogs(serviceId?: string): Promise<LogEntry[]>
  onHealthUpdate(cb: (health: OverallHealth) => void): () => void

  // App lifecycle
  getAppConfig(): Promise<RuntimeConfig>
  checkForUpdates(): Promise<UpdateInfo | null>

  // File system (local mode)
  openExternal(url: string): Promise<void>
}

class TauriBridge implements PlatformBridge {
  async getServiceStatus() {
    return await invoke<ProcessInfo[]>('get_service_status')
  }
  onHealthUpdate(cb) {
    return listen('health-update', (event) => cb(event.payload))
  }
}

class WebBridge implements PlatformBridge {
  // Web mode: service management methods throw UnsupportedError
  async getServiceStatus() {
    throw new Error('Not available in web mode')
  }
}
```

### 3.7 What Gets Removed

| Removed | Replacement |
|---------|-------------|
| `desktop/src/renderer/` (entire directory) | Components migrated to `frontend/` |
| `desktop/src/renderer/App.tsx` page routing | `frontend/` React Router |
| `desktop/src/preload/index.ts` | Tauri invoke/listen (no preload needed) |
| `desktop/src/shared/ipc-channels.ts` | `platform.ts` abstraction |

---

## Phase 4: Electron to Tauri 2

### 4.1 Why Tauri 2

| | Electron | Tauri 2 |
|--|----------|---------|
| Shell size | ~150MB (bundles Chromium) | ~5MB (uses system WebView) |
| Runtime memory | High | Low (WKWebView on macOS) |
| Frontend reuse | React works | React works |
| Process management | Node child_process | Rust std::process::Command |
| Cross-platform | Win/Mac/Linux | Win/Mac/Linux |
| Native feel | Average | Better (native WebView) |

### 4.2 Project Structure

```
tauri/                              # new, replaces desktop/
+-- src-tauri/                      # Rust backend (Tauri shell)
|   +-- Cargo.toml
|   +-- tauri.conf.json             # app config, windows, permissions
|   +-- capabilities/               # Tauri 2 permission declarations
|   |   +-- default.json
|   +-- src/
|   |   +-- main.rs                 # entry point
|   |   +-- commands/               # IPC commands
|   |   |   +-- mod.rs
|   |   |   +-- service.rs          # service management
|   |   |   +-- health.rs           # health checks
|   |   |   +-- config.rs           # configuration
|   |   |   +-- setup.rs            # setup wizard
|   |   +-- sidecar/                # Python process management
|   |   |   +-- mod.rs
|   |   |   +-- process_manager.rs
|   |   |   +-- health_monitor.rs
|   |   |   +-- python_runtime.rs
|   |   +-- state.rs                # global app state
|   |   +-- tray.rs                 # system tray
|   |   +-- updater.rs              # auto-updater
|   +-- icons/
|
+-- frontend/ -> symlink or direct reference to ../frontend/
```

### 4.3 Electron to Tauri Concept Mapping

| Electron | Tauri 2 | Code Mapping |
|----------|---------|-------------|
| `ipcMain.handle()` | `#[tauri::command]` | `ipc-handlers.ts` -> `commands/*.rs` |
| `ipcRenderer.invoke()` | `invoke()` from `@tauri-apps/api` | `window.nexus.*` -> TauriBridge |
| `ipcRenderer.on()` | `listen()` from `@tauri-apps/api/event` | Event listeners |
| `BrowserWindow` | `WebviewWindow` | Window management |
| `child_process.spawn()` | `std::process::Command` | Process management |
| `electron-updater` | `tauri-plugin-updater` | Auto-update |
| `Tray` | `tauri::tray::TrayIconBuilder` | System tray |
| `shell.openExternal()` | `tauri-plugin-shell` | Open external links |
| `app.getPath('userData')` | `app_data_dir()` | Data directory |
| `contextBridge` / `preload.ts` | Not needed (Tauri auto-injects invoke) | Removed |

### 4.4 Rust Core Modules

**Process Manager** (`sidecar/process_manager.rs`):

```rust
pub struct ServiceProcess {
    pub service_id: String,
    pub label: String,
    pub status: ServiceStatus,       // Stopped | Starting | Running | Crashed
    pub pid: Option<u32>,
    pub restart_count: u32,
}

pub struct ProcessManager {
    services: HashMap<String, ServiceProcess>,
    log_buffer: HashMap<String, VecDeque<LogEntry>>,  // max 500 per service
    app_handle: AppHandle,
}

impl ProcessManager {
    pub async fn start_service(&mut self, def: &ServiceDef) -> Result<()>;
    pub async fn start_all(&mut self, defs: &[ServiceDef]) -> Result<()>;
    pub async fn stop_all(&mut self) -> Result<()>;
    fn schedule_restart(&mut self, service_id: &str);  // max 3, exponential backoff
}
```

**Health Monitor** (`sidecar/health_monitor.rs`):

```rust
pub struct HealthMonitor {
    interval: Duration,              // 5 seconds
    debounce_threshold: u32,         // 2 consecutive unhealthy before downgrade
    service_health: HashMap<String, HealthState>,
}

impl HealthMonitor {
    pub fn start(&self, app_handle: AppHandle);  // spawns tokio task
    async fn check_service(&self, def: &ServiceDef) -> HealthState;  // TCP + HTTP
}
```

**IPC Commands** (`commands/service.rs`):

```rust
#[tauri::command]
async fn get_service_status(state: State<'_, AppState>) -> Result<Vec<ProcessInfo>, String>;

#[tauri::command]
async fn start_all_services(state: State<'_, AppState>) -> Result<(), String>;

#[tauri::command]
async fn restart_service(service_id: String, state: State<'_, AppState>) -> Result<(), String>;

#[tauri::command]
async fn get_logs(service_id: Option<String>, state: State<'_, AppState>) -> Result<Vec<LogEntry>, String>;

#[tauri::command]
async fn get_app_config(state: State<'_, AppState>) -> Result<AppConfig, String>;
```

**App State** (`state.rs`):

```rust
pub struct AppConfig {
    pub mode: AppMode,               // Local | CloudApp
    pub api_base_url: String,        // "http://localhost:8000" | "https://api.xxx.com"
    pub user_type: UserType,         // Internal | External
    pub db_path: Option<PathBuf>,    // SQLite path for local mode
    pub python_path: Option<PathBuf>,// Python runtime path
}

pub struct AppState {
    pub config: AppConfig,
    pub process_manager: Mutex<ProcessManager>,
    pub health_monitor: HealthMonitor,
    pub service_defs: Vec<ServiceDef>,
}
```

### 4.5 Local Mode Startup Sequence

```
User double-clicks NarraNexus.app
    |
    +-- First launch?
    |   +-- Yes -> Mode selection (/mode-select)
    |   |       +-- "Local" -> Setup Wizard
    |   |       +-- "Cloud" -> Login page
    |   +-- No -> Load saved config
    |
    +-- Local mode startup:
    |   1. Detect Python runtime (bundled sidecar)       ~1s
    |   2. Initialize SQLite database + migrations       ~1-2s
    |   3. Start Python backend (uvicorn, port 8000)     ~3s
    |   4. Wait for backend health (GET /health -> 200)  ~1-2s
    |   5. Start MCP Server                              ~2s
    |   6. Start Module Poller + Job Trigger             ~1-2s
    |   7. Start MessageBusTrigger (or poller-integrated)
    |   8. WebView loads frontend                        ~1s
    |   Total target: ~10-15 seconds
    |
    +-- Cloud mode startup:
        1. WebView loads frontend
        2. Frontend loads runtime config
        3. Frontend points to cloud API
        4. Restore or request authenticated session
        Total target: ~3-5 seconds
```

Compared to current: eliminates Docker startup for packaged local mode, removes Synapse/NexusMatrix bootstrap, and avoids first-run dependency installation. EverMemOS is explicitly excluded from local mode and remains cloud-only.

### 4.6 Python Runtime Bundling

**Strategy: Pre-install Python + virtualenv into app bundle.**

```
NarraNexus.app/
+-- Contents/
    +-- Resources/
        +-- python/                  # standalone Python 3.13
        |   +-- bin/python3
        |   +-- lib/python3.13/
        +-- venv/                    # pre-installed dependencies
        |   +-- lib/python3.13/site-packages/
        |       +-- uvicorn/
        |       +-- fastapi/
        |       +-- numpy/
        |       +-- aiosqlite/
        |       +-- ...
        +-- project/                 # Python project source
            +-- src/xyz_agent_context/
            +-- backend/
            +-- pyproject.toml
```

**Size estimate:**

| Component | Size |
|-----------|------|
| Python 3.13 standalone | ~45MB |
| Virtual environment (local packaged deps) | ~120-180MB |
| Project source code | ~5MB |
| Tauri shell | ~5MB |
| Frontend build artifacts | ~3MB |
| **Total DMG size** | **~180-250MB (verify in CI)** |

Compared to current: Electron shell alone is 150MB, plus user must install Docker (2GB+), Python, Node.js, etc.

### 4.7 Tauri Configuration

```json
{
  "productName": "NarraNexus",
  "identifier": "com.narranexus.app",
  "build": {
    "distDir": "../frontend/dist",
    "devUrl": "http://localhost:5173"
  },
  "app": {
    "windows": [{
      "title": "NarraNexus",
      "width": 1200,
      "height": 800,
      "minWidth": 900,
      "minHeight": 600,
      "titleBarStyle": "Overlay",
      "decorations": true
    }],
    "trayIcon": {
      "iconPath": "icons/tray.png",
      "tooltip": "NarraNexus"
    }
  },
  "bundle": {
    "active": true,
    "targets": ["dmg", "app"],
    "icon": ["icons/icon.icns"],
    "resources": [
      "resources/python/**",
      "resources/venv/**",
      "resources/project/**"
    ],
    "macOS": {
      "minimumSystemVersion": "12.0"
    }
  },
  "plugins": {
    "updater": {
      "endpoints": [
        "https://github.com/NetMindAI-Open/NarraNexus/releases/latest/download/latest.json"
      ]
    },
    "shell": { "open": true }
  }
}
```

### 4.8 What Gets Removed

| Removed | Replacement |
|---------|-------------|
| `desktop/` (entire directory) | `tauri/src-tauri/` (Rust) |
| Electron (~150MB) | Tauri shell (~5MB) |
| `node-pty` dependency | Rust `std::process::Command` |
| `electron-updater` | `tauri-plugin-updater` |
| `electron-vite` | Tauri directly references `frontend/dist` |
| `@electron-toolkit/*` | Tauri native APIs |

---

## Phase 5: Build, Release, and CI/CD

### 5.1 Build Pipeline (GitHub Actions)

```yaml
# .github/workflows/build-desktop.yml
name: Build Desktop App

on:
  push:
    tags: ['v*']

jobs:
  build-macos:
    runs-on: macos-latest
    steps:
      # 1. Prepare Python runtime
      - name: Download standalone Python
        run: |
          wget https://github.com/indygreg/python-build-standalone/releases/...
          tar xzf cpython-3.13-macos-aarch64.tar.gz -C tauri/src-tauri/resources/python

      # 2. Install Python dependencies
      - name: Create venv with dependencies
        run: |
          tauri/src-tauri/resources/python/bin/python3 -m venv tauri/src-tauri/resources/venv
          tauri/src-tauri/resources/venv/bin/pip install -e "." --no-cache-dir

      # 3. Build frontend
      - name: Build frontend
        run: cd frontend && npm ci && npm run build

      # 4. Build Tauri (includes code signing + notarization)
      - name: Build Tauri
        uses: tauri-apps/tauri-action@v0
        with:
          projectPath: tauri
        env:
          APPLE_CERTIFICATE: ${{ secrets.APPLE_CERTIFICATE }}
          APPLE_CERTIFICATE_PASSWORD: ${{ secrets.APPLE_CERTIFICATE_PASSWORD }}
          APPLE_SIGNING_IDENTITY: ${{ secrets.APPLE_SIGNING_IDENTITY }}
          APPLE_ID: ${{ secrets.APPLE_ID }}
          APPLE_PASSWORD: ${{ secrets.APPLE_PASSWORD }}
          APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}

      # 5. Upload to GitHub Releases
      - name: Upload DMG
        uses: softprops/action-gh-release@v1
        with:
          files: tauri/src-tauri/target/release/bundle/dmg/*.dmg

  build-linux:
    runs-on: ubuntu-latest
    # Same flow, Linux Python + AppImage/deb output
```

### 5.2 Apple Notarization

Tauri's `tauri-action` has built-in notarization support. Flow:

```
Build .app -> Code sign -> Upload to Apple notarization service
-> Review (~2 min) -> Staple notarization ticket -> Package DMG
```

Users will never see "unidentified developer" warnings.

### 5.3 Auto-Update Flow

```
User opens app
    |
    +-- Tauri updater checks GitHub Releases
    |   GET https://github.com/.../releases/latest/download/latest.json
    |
    +-- New version?
        +-- Yes -> UpdateBanner: "Version x.y.z available"
        |       +-- User clicks "Update" -> download -> install -> restart
        |       +-- User ignores -> prompt again next launch
        +-- No -> Do nothing
```

### 5.4 Release Matrix

| Dimension | V1 Decision |
|-----------|-------------|
| Target platform | macOS ARM (Apple Silicon) only |
| Future platforms | Windows (Tauri native support, add CI job) |
| Artifact format | `.dmg` |
| Code signing | Apple Developer certificate |
| Notarization | Apple notarization via `tauri-action` |
| Auto-update | GitHub Releases + `tauri-plugin-updater` |
| Python runtime | `python-build-standalone` macOS aarch64 |
| Estimated DMG size | ~180MB |
| Release trigger | Git tag `v*` -> GitHub Actions auto-build |
| CI jobs required | Build (compile Rust + bundle Python + build frontend) -> Sign -> Notarize -> Smoke test (open app on runner) -> Publish to GitHub Releases |

### 5.5 Python Dependency Split

```toml
# pyproject.toml
[project]
dependencies = [
    # Core (all modes)
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pydantic>=2.12.3",
    "pydantic-settings>=2.0.0",
    "numpy>=1.26.0",
    "loguru>=0.7.3",
    "croniter>=6.0.0",
    "httpx[socks]>=1.0.0",
    "sse-starlette>=2.2.0",
    "mcp[cli]>=1.20.0",
    "fastmcp>=2.14.1",
    # LLM SDKs
    "anthropic>=0.72.0",
    "openai>=2.7.1",
    "openai-agents>=0.5.0",
    "google-genai>=1.0.0",
    "claude-agent-sdk>=0.1.6",
    # Local mode default
    "aiosqlite>=0.20.0",
]

[project.optional-dependencies]
cloud = [
    "aiomysql>=0.3.2",       # cloud MySQL
]
```

---

## Project File Changes Summary

### New Files

```
tauri/                              # Tauri 2 app shell (replaces desktop/)
src/xyz_agent_context/
    +-- message_bus/                # Agent message bus (replaces Matrix)
    |   +-- message_bus_service.py  # Abstract interface
    |   +-- local_bus.py            # Local implementation (SQLite + cursor-based delivery)
    |   +-- cloud_bus.py            # Cloud implementation (REST API)
    +-- utils/
        +-- db_backend_sqlite.py    # SQLite backend
        +-- db_backend_mysql.py     # MySQL backend (extracted)
frontend/src/
    +-- pages/SystemPage.tsx        # Dashboard migrated in
    +-- pages/SettingsPage.tsx      # Model config + execution mode
    +-- pages/ModeSelectPage.tsx    # Mode selection
    +-- lib/platform.ts            # Platform abstraction layer
    +-- stores/runtimeConfigStore.ts # Runtime config + session bootstrap
```

### Modified Files

```
frontend/src/App.tsx               # New routes
frontend/src/lib/api.ts            # Dynamic API_BASE_URL
frontend/src/stores/configStore.ts # Session-aware auth state
src/xyz_agent_context/utils/
    +-- database.py                # Pluggable backend interface
    +-- db_factory.py              # Backend selection by config
src/xyz_agent_context/services/
    +-- module_poller.py           # Add MessageBus polling (merged trigger)
backend/routes/auth.py            # Cloud-ready auth/session contract
pyproject.toml                     # Dependency split
```

### Deleted Files

```
desktop/                           # Electron app (entire directory)
docker-compose.yaml                # Docker no longer needed for local mode
related_project/NetMind-AI-RS-NexusMatrix/  # Synapse no longer needed
src/xyz_agent_context/module/matrix_module/ # Replaced by message_bus
```

---

## Local vs Cloud Capability Matrix

Local mode is a complete, fully functional product. Cloud mode adds social and shared capabilities.

| Feature | Local Mode | Cloud Mode |
|---------|-----------|------------|
| Agent chat, Awareness, Jobs | Full | Full |
| Narrative core logic | Full | Full |
| All standard Modules | Full | Full |
| Claude Code execution | User's own CLI (anyone) | Internal employees only (server-deployed) |
| API mode execution (base_url + api_key + model) | Available | Available |
| Agent-to-agent communication | Same user's agents | Cross-user |
| Public Agent interaction | N/A (single user) | Any user can interact with public agents |
| EverMemOS Module | Not available | Optional (independent Module) |
| Model/provider configuration | Full | Full |
| Offline usage | Yes | No |

**Frontend handling:** Features unavailable in a given mode are simply not rendered (e.g., EverMemOS Module does not appear in the Module list for local mode). No "disabled" states or "upgrade to cloud" prompts in V1.

---

## Migration & Cutover

**Decision: No migration. Clean install for all users.**

- The current Electron + Docker + MySQL stack has very few external users due to its high installation barrier.
- CLAUDE.md principle #2: "no backward compatibility."
- Existing users install the new Tauri app fresh. No data migration tooling, no dual-runtime coexistence.
- Old Electron app and new Tauri app can coexist on the same machine (different app bundles, different data directories).
- Matrix inbox/history data is not migrated. The new MessageBus starts with a clean slate.

---

## Migration Phases (Execution Order)

All phases are completed before any release. The phased approach is for development risk management, not incremental delivery.

| Phase | Scope | Key Risk | Mitigation |
|-------|-------|----------|------------|
| 0. Scope/Auth Lock | Cross-cutting | Ambiguous local/cloud behavior, weak cloud security model | Lock assumptions before implementation |
| 1. DB Backend | Python backend only | SQL dialect differences | Comprehensive SQLite/MySQL table and repository tests |
| 2. Message Bus | Python backend only | Cross-process delivery gaps after removing Matrix | Durable bus + trigger design, not in-memory callbacks only |
| 3. Frontend + Session Bootstrap | Frontend + API contract | Runtime config / auth drift between local and cloud | Bootstrap from runtime config + server session |
| 4. Tauri Shell | New directory, no edits to existing | Rust sidecar process management | Mirror existing service topology first, optimize later |
| 5. Build/Release | CI/CD only | Python bundling size, signing, notarization | Test on clean macOS VM and verify artifact size in CI |

Each phase is independently testable. If Phase 4 hits issues, Phases 0-3 are still valid improvements and reduce architectural risk.
