---
code_file: src/xyz_agent_context/schema/instance_schema.py
last_verified: 2026-04-10
stub: false
---

# instance_schema.py

## Why it exists

When modules were first built, instance data was defined inline in `module_schema.py`. As the instance lifecycle grew more complex (status transitions, dependency tracking, narrative links, embedding-based retrieval), the instance models needed their own home. This file was extracted from `module_schema.py` so instance persistence and module configuration could evolve independently.

It defines three layers: `ModuleInstanceRecord` (what lives in the database), `ModuleInstance` (the same record plus a live module object at runtime), and `InstanceNarrativeLink` (the many-to-many join between instances and narratives).

## Upstream / Downstream

`InstanceRepository` persists and loads `ModuleInstanceRecord`. At runtime, `ModuleService._module_impl/` upgrades a `ModuleInstanceRecord` to a `ModuleInstance` by binding the actual Python module object into `.module`. `InstanceNarrativeLinkRepository` manages `InstanceNarrativeLink` rows. The `module_schema.py` file re-exports `InstanceStatus` for backward compatibility so old importers do not break.

## Design decisions

**`ModuleInstance.module` field has `exclude=True`**: the live Python module object must never be serialized to the database. Pydantic's `model_dump()` and `model_dump(mode='json')` both skip it. This ensures that saving a `ModuleInstance` to the database via `InstanceRepository` writes only the `ModuleInstanceRecord` fields.

**`ModuleInstanceRecord` vs `ModuleInstance` split**: the clean separation means `InstanceRepository` works exclusively with `ModuleInstanceRecord` (pure data, no module class dependency). The runtime layer that needs live module objects promotes records to `ModuleInstance` after loading. This prevents the repository from ever importing module code and keeps the data layer dependency-free.

**`LinkType` enum on `InstanceNarrativeLink`**: `ACTIVE` means the instance is currently used in that narrative. `HISTORY` means it was used before (instance completed or narrative ended). `SHARED` means the instance was activated from another narrative context. This three-way distinction matters for memory loading — only `ACTIVE` links contribute to the current working context.

**`last_polled_status` and `callback_processed`**: these two fields exist solely for `ModulePoller`. The poller reads them to detect when an `in_progress` instance transitions to `completed` and to prevent duplicate callback firings. They are implementation details of the polling mechanism and should not be read by module code.

## Gotchas

**`InstanceStatus` is not `str, Enum`** (unlike `JobStatus`, `WorkingSource`, etc.). This means `instance.status == "active"` will be `False` even when the status is `ACTIVE`, because you are comparing an enum member to a string. `InstanceRepository._entity_to_row()` serializes via `.value` explicitly. If you get `status` from a row dict (raw string from the DB), construct `InstanceStatus(status_str)` before comparing.

**`rebuild_module_instance_model()`** must be called after all module classes are imported. If you create a `ModuleInstance` before this call, the forward reference to `XYZBaseModule` in the `module` field annotation is unresolved, and Pydantic will either raise a validation error or silently ignore the field. The application entry point calls this during startup.

## New-joiner traps

- There are two `ModuleInstance` classes: one in `instance_schema.py` (the authoritative one) and one in `module_schema.py` (a legacy version kept for existing callers). They are structurally similar but not the same class. `InstanceRepository` uses the one from `instance_schema.py`. If you import `ModuleInstance` from `module_schema.py` you get the legacy version.
- `ModuleInstanceRecord.routing_embedding` is a 1536-dimensional float list stored as JSON in the database. Deserializing it for every query is expensive. `InstanceRepository.vector_search()` loads all candidate instances and computes cosine similarity in Python using `numpy` — there is no database-side vector index.
