---
code_file: src/narranexus/platform/memory/__init__.py
last_verified: 2026-09-07
stub: false
---

# memory/__init__.py — the memory package surface

## Intent

Re-exports the memory engine (`MemoryEngine`), the coordinator, the record types and the kind-spec API (`register_spec`, `get_spec`, `all_kinds`, `passive_kinds`, `ensure_builtin_kinds`). Since plugin platform batch 6b.2 the six kind specs are the `builtin.memory_kinds` plugin under `plugins/` — this package no longer imports them for their registration side effect; `spec.ensure_builtin_kinds` registers the manifest-named kinds through the kernel on first lookup, so a caller that imports the memory package still finds every kind.

## 2026-09-07 — exports declare_spec / memory_kind_registry

ensure_builtin_kinds is no longer exported.
