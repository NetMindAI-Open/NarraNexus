---
code_file: scripts/data_migrations/cleanup_duplicate_pinned_artifacts.py
last_verified: 2026-07-23
stub: false
---

# cleanup_duplicate_pinned_artifacts.py — collapse duplicate pinned tabs

One-shot maintenance companion to the 2026-07-23 registration dedup fix
([[registration.py]]): rows minted BEFORE that fix (agent re-registered the
same entry file agent-scoped → extra pinned rows → duplicate immortal tabs)
are collapsed to the newest row per (agent_id, file_path). Registry-only —
workspace files are never touched, the surviving row keeps serving the same
entry. Dry-run by default, `--apply` to delete; idempotent; plain
bare-identifier SQL so it runs on SQLite and MySQL. Run inside the backend
container (`cd /app && uv run python scripts/...`). Tests:
`tests/backend/migrations/test_cleanup_duplicate_pinned_artifacts.py`.
