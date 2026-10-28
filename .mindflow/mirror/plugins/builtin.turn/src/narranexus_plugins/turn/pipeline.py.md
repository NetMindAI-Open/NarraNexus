---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/pipeline.py
last_verified: 2026-09-07
stub: false
---

# pipeline.py — `TurnPipeline`, builtin.turn's implementation of the `turn.pipeline` slot

## Why it is HERE and not in the platform (2026-09-07, round-2 G2-I3 / A2-10)

The class used to be `narranexus.platform.turn.pipeline:TurnPipeline`, with `builtin.turn`'s manifest
pointing at it — the only one of the 95 builtin `provides` refs that left its own package, and the
one place platform source named a plugin id (`Contribution("builtin.turn", …)`). Three consequences,
all of which this move fixes:

1. a distribution that EXCLUDED `builtin.turn` still shipped `TurnPipeline` in the engine wheel and
   left it importable — "excluded" only meant "not registered";
2. `turn.pipeline` is the slot whose acceptance scenario is "replace the whole AgentRuntime", yet a
   third party could not fill it: they cannot add symbols to `narranexus.platform`;
3. the exception had started to spread into tests (`builtin.frameworks.nexus_power`'s package test
   whitelisted a `narranexus.platform.turn.pipeline` prefix).

The platform reaches the class through the binding (`platform.turn.turn_pipeline_for` →
`bound_entry("turn.pipeline")`), which is also what keeps the "the platform never imports a builtin
plugin" import-linter contract true.

## What it does

Runs the seven stages in `STAGES` order for one turn: `onWill<Stage>` hooks (frozen input view) → the
strategy the profile names in `turn.pipeline.<stage>` (`UnknownEntry` if nobody provides it — a
profile naming a missing strategy fails loud) → `onDid<Stage>` (frozen output view). Stage messages
stream through unchanged; hooks run under the profile's sync budget and never fail the turn;
`services.aborted` after any stage stops the run.

`_bind_event_once` is checked at every stage boundary AND every yield, not on the first message: the
Event row is created inside Ingress, and Recall/Compose run helper LLMs while yielding nothing, so
binding on the first yielded message books their spend on the previous turn.

## What it deliberately does NOT do

Declare slots. It used to call `declare_stage_slots(registries)` in `__init__`, which built a second,
`kind`-less `Slot` for each of the seven paths; `SlotTree.declare_all` keeps whichever landed first,
so in any process that built a pipeline before boot the stage slots reported `api_version == 0` and
every `api["stage_strategy"]` check passed vacuously. Slots come from the manifest at host boot; a
test that needs them calls `load_builtins(regs, "backend")`.

Choose the profile. That is `platform.turn.resolve_profile` — it is about the turn's facts, not about
this implementation, so a replacement pipeline inherits it.
