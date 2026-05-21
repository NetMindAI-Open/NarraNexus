---
code_file: src/xyz_agent_context/services/embedding_migration_service.py
last_verified: 2026-05-21
stub: false
---

# embedding_migration_service.py — per-user embedding rebuild

## Why it exists

Backfills `embeddings_store` for a user's entities (narratives, events, jobs,
social entities) under their currently-configured embedding model. Triggered
from `/api/providers/embeddings/rebuild` and surfaced as a progress bar via
`get_status()`. Each user has an independent `MigrationProgress` so concurrent
rebuilds by different users don't stomp.

## Design decisions

- **No env fallback for the provider.** `_resolve_cfg(raise_on_gating=True)`
  in the rebuild path — a missing embedding provider is a hard error surfaced
  in `progress.error`, never a silent fall back to an env key (that bug made
  "rebuild" 401 every row on cloud).
- **status and rebuild MUST count the same set.** `get_status()` and the
  `_rebuild_*` methods share the `_*_TEXT_FILTER` WHERE fragments and the same
  `get_vectors_by_ids` existence check. Any divergence shows up as a permanent
  "N missing" the rebuild can never close.
- **Entity counts are DISTINCT on entity_id.** `instance_social_entities`
  joins `module_instances`, and the SAME entity_id can live under multiple
  instances (one social-network instance per agent) — the JOIN fans out to
  one row per (entity_id, instance_id). But `embeddings_store` is keyed on
  (entity_type, entity_id, model): one vector per entity_id. So
  `_entity_count_sql` uses `COUNT(DISTINCT ise.entity_id)` and
  `_user_entity_ids('entity')` uses `SELECT DISTINCT`. Counting raw rows
  over-counted (e.g. 124 rows / 106 distinct → permanent 18 missing).
- **`_process_rows` dedups by id.** Defensive against any fan-out JOIN —
  keeps the first row per id so a duplicated id is embedded once (saves work
  and keeps progress totals aligned with the distinct status count).
- **Only embeds rows missing for the active model.** `rows_to_process`
  excludes ids already in `get_vectors_by_ids(type, ids, model)`, so a
  re-run never re-embeds already-done rows — but failed rows stay missing and
  are retried next run (which is why embedding.py now retries 429 instead of
  failing them permanently — see [[embedding]]).

## Gotchas

- `_cleanup_before_rebuild` deletes sentinel rows (dims=0) and empty-shell
  entities (no name AND no description) — it does NOT dedup entity_ids; the
  DISTINCT counting above is what handles fan-out.
- The `is_running` guard in `rebuild_all` is in-memory per process. Single
  backend worker today, so it holds; multiple uvicorn workers would each have
  their own guard.
