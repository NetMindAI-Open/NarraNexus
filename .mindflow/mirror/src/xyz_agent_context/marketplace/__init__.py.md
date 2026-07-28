---
code_file: src/xyz_agent_context/marketplace/__init__.py
last_verified: 2026-07-24
stub: false
---

# marketplace/__init__.py — marketplace domain subpackage anchor

## Why it exists

The skill/team marketplace shipped (PR #143) with its two service files and
`_skill_marketplace_impl/` lying loose at the package root, and the vendored
first-party skills in a repo-root `marketplace_skills/` directory. That broke
the repo's domain-subpackage convention (`artifact/`, `memory/`,
`message_bus/` — a `<domain>/` package holding `*_service.py` + `_*_impl/`).
This package regroups the whole marketplace feature area:
services + private impl + `resources/marketplace_skills/` (the vendored
skills now travel with the package instead of relying on a repo-root path).

## Design decisions

- **No re-exports.** All existing consumers already import the service
  modules directly (`xyz_agent_context.marketplace.skill_marketplace_service`);
  keeping the `__init__` inert made the regrouping a pure move with zero
  import-style churn. If a public seam is wanted later, follow
  `artifact/__init__.py`'s re-export pattern.
- **`_skill_marketplace_impl/` is not yet a sealed boundary.** Six call
  sites outside the package (backend/routes/skills.py, skill_module,
  skill_sync_service, both seeds) import the private impl directly —
  pre-existing debt this move did not introduce or worsen. Renaming
  impl symbols WILL break external callers until they converge on the
  service seam (todo recorded).
- **Seed path** lives in `marketplace/_skill_marketplace_seed.py` (moved
  here from `repository/` 2026-07-24 — it is marketplace bootstrap, and
  repository/ is the bottom layer), which resolves `resources/
  marketplace_skills/` next to itself (env-overridable via
  `MARKETPLACE_SKILLS_DIR`).
