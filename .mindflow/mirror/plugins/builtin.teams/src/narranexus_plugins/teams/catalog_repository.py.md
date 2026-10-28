---
code_file: plugins/builtin.teams/src/narranexus_plugins/teams/catalog_repository.py
last_verified: 2026-09-04
stub: false
---

# team_catalog_repository.py

CRUD over `team_catalog` (cloud-authoritative index). Mirrors
SkillCatalogRepository conventions: compact JSON for the categories list,
`save_template` upsert keyed on the stable `template_id` (never resets the
`downloads` counter on re-publish). `list_enabled` orders by
(sort_order, template_id) for a stable grid. Methods are named
`save_template`/`remove` (not `upsert`/`delete`) to avoid overriding
BaseRepository's incompatible `upsert(entity)->int` / `delete(id)->int`
signatures.

Batch 6b.3: moved from `platform/repository/team_catalog_repository.py`; imports `BaseRepository` absolutely.
