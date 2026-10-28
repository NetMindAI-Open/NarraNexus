---
code_file: src/narranexus/platform/bindings_runtime.py
last_verified: 2026-09-07
stub: false
---

# platform/bindings_runtime.py — resolving bindings at boot

`resolve_runtime_bindings(distribution, registries, environ, home, snapshot)`: default < distribution < `<plugin home>/narranexus.toml` < `NX_BIND__*` env; installs the result with `Registries.set_bindings` and snapshots `run/bindings.resolved.json`. Called by the backend, mcp and workers boots. `config_path()` names the user config file.

## 2026-09-07 — partial installation on unbound slots

Resolves non-strictly: every binding that did resolve is installed and each unbound slot is logged by name (its consumers fail loudly via bound_entry). Never runs the host on a different configuration than the operator wrote.
