---
code_file: src/narranexus/platform/module_system/slots.py
last_verified: 2026-09-07
stub: false
---

# src/narranexus/platform/module_system/slots.py

## 2026-09-07 — slot names only

The module system reads four slots by name; what fills them is each plugin's manifest. This replaces module_system/contributions.py, which had become a six-table roster of every builtin (module, trigger, channel, data-access, hook and service specs plus 25 hand-written PLUGIN_* constants the manifests pointed back at) — a platform→plugin dependency the import-linter could not see because it was made of strings.
