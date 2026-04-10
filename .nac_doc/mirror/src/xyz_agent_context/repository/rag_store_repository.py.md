---
code_file: src/xyz_agent_context/repository/rag_store_repository.py
last_verified: 2026-04-10
stub: false
---

# rag_store_repository.py

## Why it exists

`RAGStoreRepository` manages the `instance_rag_store` table, which tracks the mapping between a `GeminiRAGModule` instance and its Gemini File Search Store. Beyond the simple store record, it provides keyword management (for deciding whether retrieval is useful) and uploaded file tracking (for the user-facing file list in the RAG panel).

## Upstream / Downstream

`GeminiRAGModule` calls `get_or_create_store_for_instance()` on initialization and `add_uploaded_file_by_instance()` / `update_keywords_by_instance()` when files are uploaded. `GeminiRAGModule.hook_data_gathering()` calls `get_keywords_by_instance()` to inject keywords into `ContextData.rag_keywords`. The RAG file management API routes use the old `(agent_id, user_id)` -based methods for backward compatibility.

## Design decisions

**Dual query families (`_by_instance` vs non-`_by_instance`)**: the original design queried by `(agent_id, user_id)` using `display_name = "agent_{agent_id}"`. When instances were introduced (December 2025), a new family of `_by_instance` methods was added that query by `instance_id`. Both families exist and both work. New code should always use the `_by_instance` family. The old family exists only for backward compatibility with existing data that was created before instances.

**`id_field = "id"`** (auto-increment): same pattern as other repositories with mismatched id fields. All external lookups go through custom methods. The base class `get_by_id()` is not used.

**`get_keywords_by_instance()` truncates by `file_count * 10`**: the intent is to provide proportionally more keywords for stores with more files. A store with 1 file gets at most 10 keywords; a store with 2 files gets at most 20. This heuristic prevents the keyword list from growing unboundedly as files accumulate.

**`update_store_by_instance()` sets `updated_at` automatically**: every call to this method forces `updated_at = utc_now()`. Callers cannot pass a custom `updated_at`.

## Gotchas

**`get_store()` (old family) uses `display_name = "agent_{agent_id}"`** — not `"agent_{agent_id}_user_{user_id}"` despite the docstring and schema suggestion. The `user_id` parameter is accepted but silently ignored in the lookup. This was a design inconsistency that was never fixed when `user_id` support was dropped for the old path.

**`keywords` can contain both strings and dicts**: `{"keyword": "...", "score": 0.8}` vs `"plain string"`. The `get_keywords_by_instance()` method handles both formats but the behavior diverges based on the `score` parameter. If `score=True` and the list contains a mix of strings and dicts, the method returns the raw mixed list. Callers that expect a list of strings must ensure `score=False`.

## New-joiner traps

- The `display_name` in the database is NOT the human-readable name of the store. For instance-based stores it is `"instance_{instance_id}"`. For old agent-based stores it is `"agent_{agent_id}"`. The human label is `store_name` (the Gemini resource name).
- `add_uploaded_file_by_instance()` is not idempotent in terms of Gemini state — it only updates the local record. The actual file upload to Gemini must be done separately by `GeminiRAGModule`. Calling this method does not upload anything.
