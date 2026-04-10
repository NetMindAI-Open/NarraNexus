# utils/__init__.py

Public API surface for the `utils` package — re-exports the most-used utilities so callers write `from xyz_agent_context.utils import X` rather than drilling into submodules.

## Why it exists

The `utils` package contains many small modules. Without an `__init__.py` that aggregates the public exports, callers would need to know which submodule each utility lives in and import from there directly. This creates fragile import paths that break if a utility is moved to a different submodule. The `__init__.py` acts as a stable public interface: it re-exports `AsyncDatabaseClient`, `DatabaseClient` (alias), `DataLoader`, `EmbeddingClient`, `get_embedding`, embedding vector helpers, text utilities, `with_retry`, the DB factory functions, timezone utilities, and the exception hierarchy.

## Upstream / Downstream

**Re-exports from:** `database.py`, `dataloader.py`, `db_factory.py`, `retry.py`, `text.py`, `timezone.py`, `exceptions.py`, and `agent_framework/llm_api/embedding.py` (embedding is logically a utility but lives in the agent framework layer).

**Consumed by:** `repository/`, `narrative/`, `module/`, `agent_runtime/`, `backend/routes/` — anything that needs database access, embedding, or utility functions imports from `xyz_agent_context.utils`.

## Design decisions

**`DatabaseClient = AsyncDatabaseClient` alias.** Legacy code and some module implementations use the name `DatabaseClient`. Rather than a deprecation warning, a simple alias keeps backward compatibility without maintaining two implementations.

**Embedding utilities proxied from `agent_framework/`.** `EmbeddingClient`, `get_embedding`, `cosine_similarity`, and `compute_average_embedding` technically belong to the LLM framework layer but are re-exported here because they are used as utilities by Repository classes and Narrative modules. This makes `utils/__init__.py` the single import location for all common operations.

**`__all__` is exhaustive.** Every exported symbol is listed in `__all__`. This makes `from xyz_agent_context.utils import *` safe and also serves as a manifest of what the package publicly exposes.

## Gotchas

**Adding a utility to a submodule without updating `__init__.py` means it is not part of the public API.** Callers can still import directly from the submodule, but the utility will not appear in IDE autocomplete for `from xyz_agent_context.utils import ...`.

**The embedding import crosses layer boundaries.** `agent_framework/llm_api/embedding.py` is re-exported from `utils/__init__.py`. If the embedding module is refactored or renamed, this import will break `utils/__init__.py` even though the change is in a different layer. Be aware of this coupling when moving embedding code.

**New-contributor trap.** `DatabaseClient` and `AsyncDatabaseClient` are the same object. Code that checks `isinstance(obj, DatabaseClient)` and code that checks `isinstance(obj, AsyncDatabaseClient)` are checking against the same class — there is no separate `DatabaseClient` class.
