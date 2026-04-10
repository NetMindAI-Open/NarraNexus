---
code_file: src/xyz_agent_context/schema/rag_store_schema.py
last_verified: 2026-04-10
stub: false
---

# rag_store_schema.py

## Why it exists

`GeminiRAGModule` offloads knowledge base retrieval to Google's Gemini File Search API. The API creates a server-side "store" that holds uploaded files, and returns an opaque `store_name` resource identifier. This schema persists the mapping between an agent-user pair (or module instance) and their Gemini store, along with a keyword summary that lets the agent decide at query time whether retrieval is worth attempting.

## Upstream / Downstream

`RAGStoreRepository` is the sole persistence path. `GeminiRAGModule.hook_data_gathering()` calls the repository to fetch `keywords` and injects them into `ContextData.rag_keywords` so the LLM has a budget of searchable topics. When the user uploads a file, `GeminiRAGModule` calls the repository to record the filename in `uploaded_files` and update `keywords` via a separate LLM call using `KeywordsUpdateRequest`.

## Design decisions

**`display_name` as a human-readable unique key** (`agent_{agent_id}_user_{user_id}` or `instance_{instance_id}`): this was the original lookup key before `instance_id` was introduced. The old `get_store()` method still uses `display_name` for backward compatibility. New code should use `get_store_by_instance()` which looks up by `instance_id`.

**`keywords: List[Union[str, dict]]` — mixed type list**: the keywords field was initially `List[str]`. Later, a scoring feature was added that stored keywords as `{"keyword": "...", "score": 0.8}` dicts. Both formats coexist in the database. `RAGStoreRepository.get_keywords_by_instance()` handles the polymorphism: if `score=False`, it extracts the string either way; if `score=True`, it returns the raw mixed list.

**`file_count` is a denormalized counter** kept in sync manually rather than computed by `COUNT(*)` on uploaded files. This avoids a query to count the list length, but it means `file_count` can drift out of sync with `len(uploaded_files)` if file operations fail partway through.

**`KeywordsUpdateRequest` is an LLM output struct** with `min_items=5` and `max_items=20` constraints. It is passed as a structured output schema to the LLM when generating updated keywords after a file upload. The `reasoning` field captures the LLM's explanation for the keyword selection.

## Gotchas

**Dual query paths for the same data**: the `get_store()` / `update_store()` / `add_uploaded_file()` family uses `display_name` as the lookup key. The `get_store_by_instance()` / `update_store_by_instance()` / `add_uploaded_file_by_instance()` family uses `instance_id`. They operate on the same table. If you create a store via the `instance_id` path, the old `display_name`-based getters will still find it (assuming `display_name` was set), but the formats differ (`instance_{instance_id}` vs `agent_{agent_id}`). Be consistent about which family you use for a given context.

**`get_keywords_by_instance()` truncates based on `file_count`**: it returns `keywords[:min(file_count * 10, len(keywords))]`. If `file_count` is 0 (e.g., store was just created), the method returns an empty list even if `keywords` has entries. This can happen after a manual database reset of `file_count`.

## New-joiner traps

- `RAGStoreModel.store_name` is the Gemini API resource name (e.g., `fileSearchStores/abc123`), not a human label. Do not confuse it with `display_name`.
- The `uploaded_files` list stores just filenames without paths. The actual file content lives in Gemini's cloud storage, not locally. Deleting a filename from this list does not delete the file from Gemini — that requires a separate API call.
