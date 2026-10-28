---
code_file: src/narranexus/kernel/plugins/bound.py
last_verified: 2026-09-07
stub: false
---

# kernel/plugins/bound.py — reading bindings at runtime

`bound_provider/bound_layer` (binding or slot default), `bound_entry` (one-arity: the bound plugin's contribution matched by owner, by contribution name, or by `owner:name` — the form `narranexus bind` writes; `UnknownEntry` when nothing matches — loud, never a silent fallback), `bound_entries` (many-arity: filtered and ordered by the binding, else registration order). The seam every consumer of `Registries.bindings` goes through.

## 2026-09-07 — `owner:name` is the third binding-value form

A binding value may be a plugin id (owner), a contribution name, or `owner:name`. `_matches` accepts all three; the CLI writes the third, `catalog.toml_template` shows it, and `distribution.resolve_distribution` validates it through `bindings.referenced_plugins`. The earlier text ('by owner or name') described a matcher that made the CLI's own output resolve to nothing and the system prompt collapse.
