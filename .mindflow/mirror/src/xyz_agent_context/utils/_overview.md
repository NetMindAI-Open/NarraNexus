# utils/

Infrastructure utilities shared by every other layer of `xyz_agent_context` — database access, configuration, retry, timezone handling, and more.

## Directory role

`utils/` is the project's lowest-level shared library. It has no knowledge of Narratives, Modules, or Agent pipelines. Every other directory in `src/xyz_agent_context/` is a consumer of `utils/`, never the other way around. The most critical cluster is the database stack, grouped under `utils/db/` since 2026-07-24: `db/schema_registry.py` defines table shapes, `db/db_backend.py` defines the driver interface, `db/db_backend_sqlite.py` / `db/db_backend_mysql.py` / `db/db_backend_sqlite_proxy.py` are the three concrete drivers, `db/database.py` provides the application-facing client plus the MySQL-to-SQLite dialect translator, `db/db_factory.py` manages the process-wide singleton, `db/dataloader.py` batches N+1 reads, and `db/sqlite_proxy_server.py` is the desktop proxy process (run.sh / Makefile / Tauri sidecar entrypoint `xyz_agent_context.utils.db.sqlite_proxy_server`).

## Key file index

| File | Role |
|---|---|
| `db/` | The whole database stack (see the db/ mirrors): client, dialect backends ×3, factory, schema registry, dataloader, sqlite proxy server |
| `settings.py` | (in parent dir) `Settings` singleton via `pydantic-settings` |
| `service_logger.py` | One-call rotating file logger setup for background services |
| `mcp_executor.py` | Transport-agnostic MCP tool invocation utility |
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
