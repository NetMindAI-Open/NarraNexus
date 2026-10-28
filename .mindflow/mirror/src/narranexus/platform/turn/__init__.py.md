---
code_file: src/narranexus/platform/turn/__init__.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-04（批 3a）— 回合流水线包入口

导出 `TurnPipeline`、`resolve_profile`、内置 profile。

Batch 6b.2b: only the `PIPELINE_CONTRIBUTION` is registered here; strategies and profiles come from the `builtin.turn` plugin.

## 2026-09-07 — no import-time registration

ensure_pipeline_registered (called at package import, into the process registries) is gone; PIPELINE_CONTRIBUTION is named by builtin.turn's manifest.

## 2026-09-07（round-2 G2-I3 / A2-10）— the package re-exports seams, not the implementation

`TurnPipeline` and `PIPELINE_CONTRIBUTION` are no longer here: the class lives in
`narranexus_plugins.turn.pipeline` and the platform gets it via `turn_pipeline_for` (the
`turn.pipeline` binding). What this package exports is what the platform owns —
`resolve_profile` / `turn_when_context` / `PROFILES_SLOT` / `PIPELINE_SLOT`. Importing it registers
nothing.
