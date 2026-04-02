# NarraNexus Desktop App Migration Design

**Date:** 2026-04-02
**Branch:** `design/tauri-migration`
**Status:** Draft

## Goal

Transform NarraNexus from a developer-oriented Electron launcher (requiring Docker, Python, Node.js, etc.) into a **single DMG** that any non-technical user can install and use immediately. Support three runtime modes: local standalone, cloud app, and cloud web.

## Current Pain Points

| Problem | Cause |
|---------|-------|
| Final UI opens in external browser | Electron shell is only a launcher; the real chat UI is a separate Vite app at `localhost:8000` |
| Installation requires 15-30 minutes of setup | Preflight requires: Docker Desktop, uv, Python >= 3.13, Node.js >= 20, Claude Code CLI |
| Docker is overkill | Only runs MySQL 8.0 and Synapse (Matrix homeserver) |
| ~2GB+ of dependencies | Docker Desktop alone is 2GB+ |

## Target User Experience

```
Download NarraNexus.dmg (~180MB)
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
| **Cross-user comms** | No (single user) | Yes | Yes |
| **Claude Code** | User's own (anyone) | Internal employees only | Internal employees only |
| **API Mode** | Optional | All users | All users |
| **System page** | Visible | Hidden | Hidden |
| **Auto-update** | Tauri updater | Tauri updater | N/A |
| **Offline** | Yes | No | No |

---

## Phase 1: MySQL to SQLite (Pluggable Database Backend)

### 1.1 Strategy

Create a pluggable database backend behind the existing `AsyncDatabaseClient` interface. Upper layers (Repository, Service, AgentRuntime) require zero changes.

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
| `utils/database.py` | Refactor into pluggable backend interface |
| `utils/db_backend_sqlite.py` | New: SQLite backend implementation |
| `utils/db_backend_mysql.py` | New: extracted from current database.py |
| `utils/db_factory.py` | Select backend by DATABASE_URL scheme |
| `repository/base.py` | Adapt UPSERT syntax (backend handles internally) |
| `repository/narrative_repository.py` | Add narrative_participants table, remove JSON_CONTAINS |
| `repository/embedding_store_repository.py` | True batch upsert |
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
    +-- LocalMessageBus  (local mode)   <- SQLite + asyncio events
    +-- CloudMessageBus  (cloud mode)   <- REST API to cloud backend
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
    last_read_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (channel_id, agent_id)
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
```

**Real-time notification mechanism:**

```python
class LocalMessageBus(MessageBusService):
    _subscriptions: Dict[str, List[Callable]]  # channel_id -> callbacks

    async def send_message(self, from_agent, to_channel, content, msg_type="text"):
        message_id = generate_id("msg")
        await self._db.insert("bus_messages", {
            "message_id": message_id,
            "channel_id": to_channel,
            "from_agent": from_agent,
            "content": content,
            "msg_type": msg_type,
        })
        # Event-driven: trigger callbacks immediately (no polling!)
        for callback in self._subscriptions.get(to_channel, []):
            asyncio.create_task(callback(message))
        return message_id
```

Key performance advantage over MatrixTrigger: **zero polling, event-driven**. Message delivery latency drops from 15-120 seconds to milliseconds.

**Unread query (optimized):**

```sql
SELECT m.* FROM bus_messages m
JOIN bus_channel_members cm ON m.channel_id = cm.channel_id
WHERE cm.agent_id = ?
  AND m.created_at > cm.last_read_at
ORDER BY m.created_at ASC;
```

**Agent discovery:** Reuses existing VectorStore + numpy cosine similarity on `capability_embedding`.

### 2.5 CloudMessageBus Implementation

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

### 2.6 Cross-User Communication (Cloud Mode)

In cloud mode, public agents can be discovered and messaged by any user.

**Permission model:**

| | Local Mode | Cloud Mode |
|--|-----------|------------|
| Communication scope | Same user's agents only | All users' agents |
| Agent discovery | Only own agents | All `visibility = 'public'` agents + own agents |
| Channel visibility | All visible (single user) | `private` (invite only) / `public` (searchable) |
| Permission control | Not needed | `register_agent()` declares `visibility: public/private` |

### 2.7 Public Agent Interaction Model

Agents marked `is_public = true` can be interacted with by any user in cloud mode.

**Security approach:** Instead of hard-coding access rules, the agent is informed of the caller's identity via message context. The agent uses its own cognitive ability (Awareness) to decide how to respond.

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

This aligns with the Awareness-driven design philosophy of the project.

### 2.8 MCP Tool Mapping

| Original Matrix Tool | New MessageBus Method | Change |
|---------------------|----------------------|--------|
| `matrix_send_message` | `bus.send_message()` | Simplified params |
| `matrix_create_room` | `bus.create_channel()` | room -> channel |
| `matrix_search_agents` | `bus.search_agents()` | Interface unchanged |
| `matrix_get_inbox` | `bus.get_unread()` | Interface unchanged |
| `matrix_register` | `bus.register_agent()` | Interface unchanged |

Module rename: `MatrixModule` -> `MessageBusModule`.

### 2.9 What Gets Removed

| Removed | Reason |
|---------|--------|
| Synapse service in `docker-compose.yaml` | No longer needed |
| `related_project/NetMind-AI-RS-NexusMatrix/` | No longer needed |
| `matrix_credentials` table | Replaced by `bus_agent_registry` |
| `matrix_processed_events` table | Event-driven model needs no dedup table |
| MatrixTrigger background process | Replaced by asyncio callbacks (one fewer process) |
| `matrix-nio` dependency | No longer needed |

**Net effect: one fewer Docker container, one fewer background process, one fewer external dependency, lower latency.**

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

### 3.4 Mode Detection and Feature Gating

```typescript
type AppMode = 'local' | 'cloud-app' | 'cloud-web'

function detectAppMode(): AppMode {
  if (window.__TAURI__) {
    const config = await invoke('get_app_config')
    return config.mode  // 'local' | 'cloud-app'
  }
  return 'cloud-web'
}
```

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
  getAppMode(): Promise<AppMode>
  getAppConfig(): Promise<AppConfig>
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
    |   2. Initialize SQLite database (auto-create)      ~1s
    |   3. Start Python backend (uvicorn, port 8000)     ~3s
    |   4. Wait for backend health (GET /docs -> 200)    ~2s
    |   5. Start MCP Server                              ~2s
    |   6. Start Module Poller                           ~1s
    |   7. WebView loads frontend                        ~1s
    |   Total: ~10 seconds
    |
    +-- Cloud mode startup:
        1. WebView loads frontend
        2. Frontend points to cloud API
        3. Show login page
        Total: ~3 seconds
```

Compared to current: eliminates Docker startup (~30s), Synapse wait (~90s), dependency installation (5-20 min). Cold start drops from 2-3 minutes (best case) to ~10 seconds.

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
| Virtual environment (all deps) | ~120MB |
| Project source code | ~5MB |
| Tauri shell | ~5MB |
| Frontend build artifacts | ~3MB |
| **Total DMG size** | **~180MB** |

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

### 5.4 Python Dependency Split

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
    |   +-- local_bus.py            # Local implementation (SQLite + asyncio)
    |   +-- cloud_bus.py            # Cloud implementation (REST API)
    +-- utils/
        +-- db_backend_sqlite.py    # SQLite backend
        +-- db_backend_mysql.py     # MySQL backend (extracted)
frontend/src/
    +-- pages/SystemPage.tsx        # Dashboard migrated in
    +-- pages/SettingsPage.tsx      # Model config + execution mode
    +-- pages/ModeSelectPage.tsx    # Mode selection
    +-- lib/platform.ts            # Platform abstraction layer
```

### Modified Files

```
frontend/src/App.tsx               # New routes
frontend/src/lib/api.ts            # Dynamic API_BASE_URL
src/xyz_agent_context/utils/
    +-- database.py                # Pluggable backend interface
    +-- db_factory.py              # Backend selection by config
pyproject.toml                     # Dependency split
```

### Deleted Files

```
desktop/                           # Electron app (entire directory)
docker-compose.yaml                # Docker no longer needed
related_project/NetMind-AI-RS-NexusMatrix/  # Synapse no longer needed
src/xyz_agent_context/module/matrix_module/ # Replaced by message_bus
```

---

## Migration Phases (Execution Order)

All phases are completed before any release. The phased approach is for development risk management, not incremental delivery.

| Phase | Scope | Key Risk | Mitigation |
|-------|-------|----------|------------|
| 1. DB Backend | Python backend only | SQL dialect differences | Comprehensive table tests |
| 2. Message Bus | Python backend only | Missing Matrix features | 1:1 API mapping verified |
| 3. Frontend Merge | Frontend only | Dashboard component integration | Isolated route, no side effects |
| 4. Tauri Shell | New directory, no edits to existing | Rust sidecar process management | Mirrors existing Electron logic |
| 5. Build/Release | CI/CD only | Python bundling, notarization | Test on clean macOS VM |

Each phase is independently testable. If Phase 4 hits issues, Phases 1-3 are still valid improvements.
