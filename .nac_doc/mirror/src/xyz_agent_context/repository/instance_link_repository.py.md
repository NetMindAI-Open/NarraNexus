---
code_file: src/xyz_agent_context/repository/instance_link_repository.py
last_verified: 2026-04-10
stub: false
---

# instance_link_repository.py

## Why it exists

`InstanceNarrativeLinkRepository` manages the `instance_narrative_links` junction table, which implements the many-to-many relationship between module instances and narratives. It exists because a single instance can be shared across multiple narratives (e.g., a `SocialNetworkModule` instance that tracks relationships relevant to several conversation threads), and a single narrative can have multiple instances (chat, social, RAG, job).

## Upstream / Downstream

`ModuleService._module_impl/` calls `link()` and `unlink()` when instances are added to or removed from a narrative during Step 2. `get_instances_for_narrative()` is used to load all instances associated with a narrative when building context. `get_narratives_for_instance()` is used by `ModulePoller` to propagate job completion callbacks to the right narrative contexts.

## Design decisions

**`unlink()` soft-deletes by default** (`to_history=True`): rather than deleting the link record, it changes `link_type` to `"history"` and sets `unlinked_at`. This preserves the audit trail of which instances were active in which narratives over time. Hard deletion is available via `to_history=False` but should only be used for cleanup/purge operations.

**`link()` is idempotent**: if a link already exists, it checks whether `link_type` needs updating and returns `0` (no new insert). Callers can call `link()` multiple times without creating duplicate records.

**`update_local_status()` exists alongside the global `status` in `module_instances`**: an instance has a global status (e.g., `COMPLETED`) and a per-narrative local status. A completed job instance might be globally `COMPLETED` but still show as `ACTIVE` in the narrative's local context for display purposes. The local status allows fine-grained per-narrative state without polluting the global status.

## Gotchas

**`get_instances_for_narrative()` defaults to `link_type=LinkType.ACTIVE`**: if you want all historical instances (including completed ones) for a narrative, you must explicitly pass `link_type=None`. Forgetting this when building context views will silently omit completed instances.

**`id_field = "id"`** (auto-increment): the base class `get_by_id()` and `update()` methods use the integer auto-increment ID. All meaningful queries use custom methods (`link`, `unlink`, `get_instances_for_narrative`, etc.) that build SQL directly. The base class methods are effectively unused in practice.

## New-joiner traps

- The `local_status` and global `status` (in `module_instances`) can diverge. The canonical source of truth for whether an instance has completed is the global status in `InstanceRepository`. `local_status` in the link table is a narrative-scoped view that may lag behind the global status.
- `unlink_all_for_narrative()` only affects `link_type = 'active'` links — it does not touch `history` or `shared` links. If you need to completely remove all trace of a narrative's instances, you must also delete the history links separately.
