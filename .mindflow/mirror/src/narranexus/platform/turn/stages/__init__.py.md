---
code_file: src/narranexus/platform/turn/stages/__init__.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-04（批 3a）— 默认策略的注册

七个阶段的 `Contribution` 常量（`INGRESS/RECALL/COMPOSE/ASSEMBLE/ACT/COMMIT/REFLECT`）在 import 时注册进
`KERNEL_REGISTRIES`（缺位则以 owner `builtin.turn` 声明），与 `builtin.turn` manifest 的 provides 是同一批对象，
loader 再注册是幂等 no-op（与 frameworks/providers 同一模式）。`ensure_registered(registries)` 让测试用干净
`Registries` 也能起流水线。

Batch 6b.2b: only the slot declarations remain here (`OWNER`, `STAGE_CONTRACT`, `slot_path`, `declare_stage_slots`, `ensure_registered`); the default strategies are the `builtin.turn` plugin package.

Batch 6 fix: the stage slots are kernel-declared; `declare_stage_slots` only fills gaps of a hand-built tree.

## 2026-09-07 — declare_stage_slots only

ensure_registered (lazy builtin strategies) is gone.

## 2026-09-07（round-2 P2-I7 / A2-4）— `declare_stage_slots` deleted; only the path grammar is left

The helper built a SECOND `Slot` for each of the seven `turn.pipeline.<stage>` paths with no `kind`,
and `SlotTree.declare_all` keeps whichever declaration landed first. In any process that constructed
a `TurnPipeline` before boot (a test host, a CLI, a future eager pipeline), the seven slots third
parties are meant to extend therefore reported `api_version == 0`, which made every
`api["stage_strategy"]` compatibility check on a stage plugin pass vacuously — the fail-closed gate
disabled silently. The manifest (`builtin.turn`) is now the single declaration; a test that needs the
tree calls `load_builtins(regs, "backend")` like the hosts do. What remains: `slot_path(stage)`,
`OWNER`, `STAGE_CONTRACT`. The old module docstring about "import-time registration mirrors the
frameworks/providers" described a file that has not registered anything for two batches.
