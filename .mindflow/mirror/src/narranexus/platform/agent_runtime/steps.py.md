---
code_file: src/narranexus/platform/agent_runtime/steps.py
last_verified: 2026-09-07
stub: false
---

# src/narranexus/platform/agent_runtime/steps.py

## 2026-09-07 — the public step surface for stage strategies

A stage strategy (plugins/builtin.turn or a third party) needs the step bodies the seven-stage pipeline replaced; they lived in the private _agent_runtime_steps package and two underscore helpers on agent_runtime.py, which the builtin.turn plugin imported directly — a private surface that was a contract in all but name. This module is the one public door: the step functions, step_3_assemble_context, build_after_execution_params, turn_timing_line (the grep-stable [turn-timing] line, byte-identical) and stream_with_interrupt_drain (the bounded post-cancel drain, moved verbatim). Renaming anything here is a breaking change for stage plugins; tests seam the steps by monkeypatching this module.
