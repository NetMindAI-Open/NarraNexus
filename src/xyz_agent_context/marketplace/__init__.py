"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Marketplace domain subpackage — skill + team marketplace.

Groups the marketplace feature area in one place, matching the repo's
domain-subpackage convention (artifact/, memory/, message_bus/, ...):

- `skill_marketplace_service.py` / `team_marketplace_service.py` — the
  public service seams consumers import directly.
- `_skill_marketplace_impl/` — private implementation (registry, install
  pipeline, artifact store, scanner, secret box). NOTE: several external
  consumers (backend routes, skill_module, skill_sync_service, the two
  seeds) still import it directly — pre-existing debt; long-term these
  should converge on the service seam.
- `resources/marketplace_skills/` — first-party skills vendored with the
  package, seeded into the catalog by `marketplace/_skill_marketplace_seed`.

No re-exports: consumers import the service modules explicitly.
"""
