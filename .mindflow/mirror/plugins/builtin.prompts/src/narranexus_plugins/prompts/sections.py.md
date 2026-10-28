---
code_file: plugins/builtin.prompts/src/narranexus_plugins/prompts/sections.py
last_verified: 2026-09-07
stub: false
---

# builtin.prompts — sections.py

Security (cloud only, order 10), Temporal (skipped under turn-context relocation, 20), Narrative (main narrative via NarrativeService, records `nar_*` meta, 30), Modules (runtime's module-instruction formatter, 40), Bootstrap (first-run injection, deletes Bootstrap.md past threshold, sets `ctx_data.bootstrap_active`, 50). Each renders through the `PromptContext.runtime` helpers; `CONTRIBUTIONS` is the manifest's tuple.

## 2026-09-07 — sections call the public runtime surface; Bootstrap renders only

TemporalSection / ModulesSection call the runtime's public build_* methods. BootstrapSection reads ctx_data.bootstrap_active (settled by the platform) and returns the injection prompt — it no longer deletes files or sets turn state, so a distribution that drops the section loses text, never the lifecycle.

## 2026-09-07（round-2 P2-I4）— `SecuritySection` is load-bearing on cloud

`required_in = ("cloud",)`. On cloud, dropping this section by a binding, by a `builtin_overrides`
disable of this plugin, or by one raising render now refuses the whole prompt
(`RequiredSectionMissing`) instead of shipping every turn without the iron rules and a single
warning. Local and desktop are unaffected: `render` returns `None` there BY DESIGN, which is exactly
why the flag is per deployment mode. The other four sections declare `required_in = ()` explicitly —
degradable is a decision here, not an omission.
