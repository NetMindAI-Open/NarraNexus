# xyz_agent_context/

The core installable Python package — all agent runtime logic, memory management, module system, LLM integrations, and database infrastructure.

## Directory role

`xyz_agent_context` is the `src`-layout package that `pyproject.toml` declares as the installable library. External code (the FastAPI backend, MCP tools, Tauri sidecar scripts) imports from this package. It is organized in strict dependency layers: lower layers know nothing about higher layers, and modules within the same layer do not import from each other.

```
agent_runtime/        ← orchestration (7-step pipeline), topmost layer
  ↓
agent_framework/      ← LLM SDK adapters (Claude, OpenAI, Gemini)
context_runtime/      ← context assembly engine
  ↓
narrative/            ← Narrative & Event management
module/               ← pluggable Module system
  ↓
schema/               ← Pydantic data models (shared by all layers)
utils/                ← database, config, retry, timezone, etc.
repository/           ← data-access layer (uses utils/)
services/             ← background services (ModulePoller, InstanceSyncService)
```

The two files at this root level (`settings.py`, `config.py`) are special: they are consumed by almost every other file in the package and have no dependencies within the package. `__init__.py` stitches together the public API.

## Key file index

| File | Role |
|---|---|
| `__init__.py` | Package public API — re-exports `AgentRuntime`, `NarrativeService`, `XYZBaseModule`, etc. |
| `settings.py` | `Settings` singleton — all environment variables, loaded once at import time |
| `config.py` | Static algorithm tuning constants (e.g., `NARRATIVE_LLM_UPDATE_INTERVAL`) |
| `prompts_index.py` | Consolidated index of all prompt constants across subsystems |

## Collaboration with external directories

- **`backend/`** — FastAPI routes import `AgentRuntime`, repository classes, `AsyncDatabaseClient`, and schema models directly from their submodules (not the package root) to minimize startup load.
- **`frontend/`** — no direct dependency; the frontend communicates only via the FastAPI HTTP/WebSocket API.
- **`utils/database_table_management/`** — standalone scripts that read `schema_registry.TABLES` from `utils/schema_registry.py` to create and migrate tables; not imported by the package at runtime.
