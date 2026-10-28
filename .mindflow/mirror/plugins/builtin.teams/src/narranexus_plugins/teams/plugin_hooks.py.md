---
code_file: plugins/builtin.teams/src/narranexus_plugins/teams/plugin_hooks.py
last_verified: 2026-09-04
stub: false
---

# builtin.teams — plugin_hooks.py

`backend.hooks` implementation: on `onDidStartBackend` (fired by the backend's post-start background task with the db client) a registry host (`is_registry_host()`) seeds the team marketplace via `seed_team_marketplace(db)` and returns the template count; a non-registry host returns 0. `HOOKS` is the tuple the manifest names.
