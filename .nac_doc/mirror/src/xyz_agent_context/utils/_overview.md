# utils/

Infrastructure utilities shared by every other layer of `xyz_agent_context` — database access, configuration, retry, timezone handling, and more.

## Directory role

`utils/` is the project's lowest-level shared library. It has no knowledge of Narratives, Modules, or Agent pipelines. Every other directory in `src/xyz_agent_context/` is a consumer of `utils/`, never the other way around. The most critical cluster is the database stack: `schema_registry.py` defines table shapes, `db_backend.py` defines the driver interface, `db_backend_sqlite.py` / `db_backend_mysql.py` / `db_backend_sqlite_proxy.py` are the three concrete drivers, `database.py` provides the application-facing client plus the MySQL-to-SQLite dialect translator, and `db_factory.py` manages the process-wide singleton.

## Key file index

| File | Role |
|---|---|
| `database.py` | `AsyncDatabaseClient` — the unified CRUD interface and dialect translator |
| `schema_registry.py` | `TABLES` dict — single source of truth for every table's columns and indexes |
| `db_factory.py` | `get_db_client()` singleton factory, URL-based backend selection |
| `db_backend.py` | `DatabaseBackend` ABC — the interface all backends must implement |
| `db_backend_sqlite.py` | `SQLiteBackend` — local/desktop driver via `aiosqlite` with WAL and write lock |
| `db_backend_mysql.py` | `MySQLBackend` — cloud driver via `aiomysql` connection pool |
| `db_backend_sqlite_proxy.py` | `SQLiteProxyBackend` — HTTP client that forwards all DB calls to the proxy process |
| `sqlite_proxy_server.py` | The proxy process itself — owns the exclusive SQLite connection in multi-process deployments |
| `settings.py` | (in parent dir) `Settings` singleton via `pydantic-settings` |
| `config.py` | (in parent dir) Static algorithm tuning constants |
| `service_logger.py` | One-call rotating file logger setup for background services |
| `mcp_executor.py` | Transport-agnostic MCP tool invocation utility |
| `dataloader.py` | GraphQL-DataLoader-style N+1 batcher |
| `cost_tracker.py` | Ambient `ContextVar` for recording LLM API costs per agent turn |
| `retry.py` | `@with_retry` decorator with exponential backoff |
| `timezone.py` | UTC storage / user-timezone display / LLM-friendly formatting |
| `text.py` | Keyword extraction and smart truncation for mixed Chinese-English text |
| `exceptions.py` | `AgentContextError` hierarchy — typed errors with rich context |
| `file_safety.py` | Path traversal and upload size validation helpers |
| `evermemos/` | HTTP client for the optional EverMemOS external memory service |

## Collaboration with external directories

- **`repository/`** — all Repository classes receive an `AsyncDatabaseClient` obtained from `get_db_client()` and call its CRUD methods.
- **`agent_runtime/`** — calls `set_cost_context` / `clear_cost_context` at the start and end of each turn; uses `get_db_client()` to persist events and narrative updates.
- **`module/`** — module implementations call `get_db_client()` inside MCP tool handlers; `service_logger.py` is used by `module_runner.py`.
- **`backend/routes/`** — FastAPI routes import `get_db_client()`, `AsyncDatabaseClient`, timezone formatters, and `file_safety` validators.
- **`narrative/`** — narrative and event repositories use `AsyncDatabaseClient`; `timezone.py` formats event timestamps for LLM prompts.
