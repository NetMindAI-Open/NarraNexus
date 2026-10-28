---
code_file: backend/routes/plugins/__init__.py
last_verified: 2026-08-28
stub: false
---

# backend/routes/plugins/__init__.py — route group anchor

Created alongside the local/desktop plugin-install feature (Claude Code /
Codex CLI move out of the base install into `~/.narranexus/plugins/`, see
`backend/integrations/plugins/`). Follows the same grouped-route-dir pattern
as `backend/routes/admin/`: an inert package marker, no re-exports —
`backend/main.py` imports `routes.router` explicitly.
