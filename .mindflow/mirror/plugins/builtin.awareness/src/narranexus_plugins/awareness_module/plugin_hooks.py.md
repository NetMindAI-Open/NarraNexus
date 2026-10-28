---
code_file: plugins/builtin.awareness/src/narranexus_plugins/awareness_module/plugin_hooks.py
last_verified: 2026-09-04
stub: false
---

# awareness_module/plugin_hooks.py — identity record upkeep

## Intent

`onDidChangeAgentName` → `record_identity_change`, `onDidSettleAgentName` → `reconcile_identity_record`, both on the caller's db client (the rename transaction and the bundle importer pass theirs). The platform no longer imports awareness's writers; with the plugin disabled nothing listens and the callers get None — the same answer the writers give for an agent without an AwarenessModule instance. Listener exceptions surface to the rename transaction (it logs and continues, as before).
