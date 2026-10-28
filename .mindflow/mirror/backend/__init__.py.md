---
code_file: backend/__init__.py
last_verified: 2026-09-04
stub: false
---

# backend/__init__.py

## Intent

Package marker for the FastAPI backend, and nothing else. Until 2026-09-04 it re-exported `app` by importing `backend.main`, so the first `import backend.<anything>` built the entire application (DB client, routers, plugin registries). Batch 3c.2 made builtin feature plugins expose symbols under `backend.*` (`narranexus_plugins.teams.routes:ROUTES` for `builtin.teams`); the plugin loader — and a plugin author's `PluginTestHost` — import those without wanting the app, so the re-export was removed. Every entrypoint (run.sh, Makefile, Tauri `state.rs`, compose) already names `backend.main:app` explicitly; nothing consumed `from backend import app`.

## Gotchas

- Do not add imports here. Anything that must run at app construction belongs in `backend/main.py`.
