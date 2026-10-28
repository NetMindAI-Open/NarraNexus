---
code_file: src/narranexus/platform/__init__.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— 平台层落点

「所有插件都需要且插件做不了」的东西：今天只有回合流水线 `platform.turn`。可 import 内核、契约与迁移中的
`xyz_agent_context` 域包；永不 import 插件。

## 2026-09-04 · the former package root (batch 6a, D8)

`narranexus.platform` now IS the former `xyz_agent_context` root: the domain packages moved here 1:1 (`module` → `module_system`) and the root re-exports are unchanged. `xyz_agent_context` is a one-release alias (`src/narranexus/platform/__init__.py`).

### Former `narranexus/platform/__init__.py` notes

# narranexus/platform/__init__.py

Package entry point — re-exports the full public API of the `xyz_agent_context` package in dependency order.

## 2026-08-28 — `ClaudeAgentSDK` 惰性导出（轻量化插件）

`claude-agent-sdk` 在本地版是按需安装的插件，故顶层包 import 时**不能**急加载它。
去掉 import 期 `from .agent_framework import ClaudeAgentSDK`，改为 PEP 562
`__getattr__` 惰性透传（仍 `from xyz_agent_context import ClaudeAgentSDK` 可用，但
只在真取用时才拉 SDK）。名字仍在 `__all__`。详见 [[__init__]]（agent_framework）。

## 2026-06-09 — `__version__` sourced from package metadata

`__version__` was a hardcoded `"0.1.0"` that silently went stale. It now reads
`importlib.metadata.version("xyz-agent-context")` (= pyproject `[project].version`,
one of the 5 release anchors), falling back to `"0.0.0+unknown"` in a source tree
with no install metadata. This is the single source of truth the bundle builder
stamps into every export manifest (`_current_app_version`).

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

Batch 6b.2b: the lazy `ClaudeAgentSDK` passthrough is gone; the SDK is `narranexus_plugins.frameworks_claude_code.sdk`.

Batch 6d: `__version__` comes from the `narranexus` package metadata.
