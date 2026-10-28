---
code_file: src/narranexus/kernel/plugins/distribution.py
last_verified: 2026-09-07
stub: false
---

# kernel/plugins/distribution.py — resolving a distribution

`resolve_distribution(spec, base_dir, builtins, tree, host_version)` picks builtins by id + version range and bundled plugins by path (manifest loaded against the slot tree) and collects problems instead of raising: engine range vs host, unknown plugin, range miss, missing/lying bundled manifest, excludes naming non-builtins, dependency not in the set (or excluded), `auth` not selected / not providing `kernel.auth` / not distributionOnly, binding providers outside the set. The result carries the `BindingSource(Layer.DISTRIBUTION)` (bindings + `kernel.auth = auth`). `doctor_report()` is the CLI view (plugin rows, size budget, problems); `lock_data()/write_lock()` the reproducible plugin set; `find_distribution()/resolve_from_env()` read `NARRANEXUS_DIST` (file or directory). No plugin code is imported here.

## 2026-09-07 — bindings validated through the binding grammar

resolve_distribution checks every plugin id a binding value references (via bindings.referenced_plugins) against the selected set; a bare contribution-name form is accepted when it matches a selected manifest's provided symbol name. Before this a legal 'owner:name' or '-id' binding made the whole app refuse to boot.

## 2026-09-07 — every builtin must be classified

resolve_distribution reports 'unclassified builtins' when a builtin of this engine is neither in plugins nor in excludes — builtin.prompts had fallen outside all four distributions while doctor stayed green. Bindings are validated through bindings.referenced_plugins.
