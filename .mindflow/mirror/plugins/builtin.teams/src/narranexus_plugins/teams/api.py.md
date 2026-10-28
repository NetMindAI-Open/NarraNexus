---
code_file: plugins/builtin.teams/src/narranexus_plugins/teams/api.py
last_verified: 2026-09-04
stub: false
---

# builtin.teams — api.py facade

The only import surface another plugin or a distribution may use: `TeamMarketplaceService`, `store_key_for`, `TeamTemplate`, `seed_team_marketplace`, plus `PLUGIN_ID`/`PACKAGE`. Everything else in the package is private; the host reaches the plugin through its manifest contributions (two routers, the worker, the hook).
