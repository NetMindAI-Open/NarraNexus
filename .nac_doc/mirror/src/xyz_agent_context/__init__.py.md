# xyz_agent_context/__init__.py

Package entry point — re-exports the full public API of the `xyz_agent_context` package in dependency order.

## Why it exists

`xyz_agent_context` is the core installable package. Its `__init__.py` defines what is importable from the package root (`from xyz_agent_context import AgentRuntime`). Without it, callers would have to know the deep module path for every symbol. The file also establishes the initialization order: `schema/` (no deps) → `utils/` (low deps) → `narrative/` → `module/` → `agent_framework/` → `context_runtime/` → `agent_runtime/`. This order is intentional — importing in a different order during testing can trigger circular import errors.

## Upstream / Downstream

**Re-exports from:** `schema/` (Pydantic data models), `utils/` (`DatabaseClient`), `narrative/` (`Narrative`, `Event`, `EventService`, `NarrativeService`), `module/` (`XYZBaseModule`, `ModuleService`, `HookManager`), `agent_framework/` (`ClaudeAgentSDK`), `context_runtime/` (`ContextRuntime`), `agent_runtime/` (`AgentRuntime`).

**Consumed by:** external code and tests that import the package. The FastAPI `backend/` imports specific items directly from their submodules rather than via the package root (to avoid importing everything on startup), but integration tests typically import from here.

## Design decisions

**Dependency-ordered imports.** The six `from .xxx import ...` blocks are ordered from least to most dependent. This makes the initialization order explicit and ensures that if a circular import is introduced, it fails at the most understandable level.

**`__version__ = "0.1.0"` is hardcoded.** Version management is not yet automated. Update this manually when tagging a release.

**`__all__` covers everything re-exported.** All re-exported symbols are listed in `__all__` so `from xyz_agent_context import *` works correctly in scripts and REPL sessions.

## Gotchas

**Importing this module loads the entire package.** Every `from .xxx import ...` line executes the target module, which may trigger database schema checks, settings loading, or other side effects. Tests that only need a specific submodule (e.g., `schema/`) should import from that submodule directly to avoid the startup overhead.

**New-contributor trap.** Adding a new top-level module to the package without adding it to `__init__.py` means it is not discoverable via `from xyz_agent_context import NewModule`. It can still be imported from its own path, but it will not appear in the package's public surface.
