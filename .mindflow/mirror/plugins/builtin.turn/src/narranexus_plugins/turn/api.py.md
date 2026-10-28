---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/api.py
last_verified: 2026-09-07
stub: false
---

# builtin.turn — api.py facade

The only import surface another plugin or a distribution may use (plugin platform batch 6b): the plugin id and package name. The host reaches the plugin through its manifest contributions, never through imports.

## 2026-09-07（round-2 G2-I3 / G2-I5）— the facade speaks the manifest's vocabulary

Exports `CONTRIBUTION` + `TurnPipeline` (the pipeline class moved into this package from the
platform), `PROFILES` and `STRATEGIES`. It used to export `BUILTIN_PROFILES` / `STRATEGIES` — a third
set of names for the same things, next to the manifest's and §8's.
