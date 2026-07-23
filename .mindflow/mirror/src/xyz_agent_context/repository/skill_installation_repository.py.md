---
code_file: src/xyz_agent_context/repository/skill_installation_repository.py
last_verified: 2026-07-20
stub: false
---

# skill_installation_repository.py

Audit trail of skills per (agent_id, user_id) workspace — the DB *follower*
of the filesystem truth (`skills/` + `.skill_meta.json`). Written by the
InstallPipeline on every install/update/uninstall and corrected by the
reconciler (`services/skill_sync_service.py`).

## Design decisions

- **Unique key is the (agent_id, user_id, skill_id) triple** — a workspace is
  `{agent_id}_{user_id}`, so agent_id alone would collide across users.
- **Rows are never deleted.** Uninstall/external removal flips `status`
  (`uninstalled` / `external_removed`), keeping history queryable (incident
  lesson #5: business events in the DB beat log greps).
- `mark_status()` returns `False` for a missing row instead of raising — the
  reconciler treats that as "disk has it, DB doesn't" and calls
  `upsert_event(source_type="manual")` instead.
- Timestamps are set in code (`_now()`, UTC) rather than relying on SQL
  `datetime('now')` defaults, because `db.update` binds values as parameters
  (a raw SQL expression would be stored as literal text — see the
  user_settings entry in schema_registry.py.md).

## Gotchas

- `status` semantics: `modified` = content hash drifted from install-time
  hash (unmanaged edit); `disabled` mirrors the `skills/.disabled/` move.
  The UI's "Unmanaged" badge keys off `modified`/`manual`.
