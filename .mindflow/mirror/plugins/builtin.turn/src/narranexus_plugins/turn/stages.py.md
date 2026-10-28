---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/stages.py
last_verified: 2026-09-07
stub: false
---

# builtin.turn — stages.py

Contribution tuples per stage slot — `INGRESS`, `RECALL`, `COMPOSE`, `ASSEMBLE`, `ACT`, `COMMIT`, `REFLECT` — each wrapping the default strategy of that stage as a `Contribution`; `STRATEGIES` maps slot path to tuple. The builtin manifest names these under `turn.pipeline.<stage>`; `platform.turn.stages.ensure_registered()` has the kernel register them when a slot is empty.

## 2026-09-07（round-2 G2-I5）— `<STAGE>_STRATEGIES` per API_POLICY §8

`INGRESS`/`RECALL`/… → `INGRESS_STRATEGIES`/`RECALL_STRATEGIES`/…, matching what
`templates/stage_strategy` scaffolds. §8 exists so a template composes without renaming; the most
copied builtin was the one violating it. Symbol names only — the registered contribution NAMES
(`default`, `narrative_fast`, `ephemeral`, `silent`), which profiles and bindings refer to, are
untouched.
