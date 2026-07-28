---
code_file: src/xyz_agent_context/utils/db/__init__.py
last_verified: 2026-07-24
stub: false
---

# utils/db/__init__.py — the database family, out of the grab bag

## Why it exists

`utils/` had grown to 34 loose files; 9 of them were the entire database
layer (client, dialect backends, factory, schema registry, dataloader,
sqlite proxy server). The 2026-07-24 cleanup groups them under
`utils/db/` so the db seam (which binding rule #20 wants movable behind
an abstraction) is one directory, not a filename prefix convention.

## Gotchas

- `sqlite_proxy_server` is a run.sh + Makefile entrypoint
  (`python -m xyz_agent_context.utils.db.sqlite_proxy_server`) — its
  callers were updated in the same commit (same-repo atomic; desktop
  and dev-mode only, no compose contract).
- CLAUDE.md still says "register tables in `utils/schema_registry.py`" —
  the Owner's pending layout update (PR-4 draft) carries the new
  `utils/db/schema_registry.py` path.
