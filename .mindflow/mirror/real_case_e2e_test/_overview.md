---
code_file: real_case_e2e_test/
last_verified: 2026-05-13
stub: false
---

# real_case_e2e_test — module overview

## Why it exists

The Lark Base bug tracker keeps rediscovering the same regression
families (no-response placeholders, narrative mismatches after short
replies, team-owner inconsistency, etc.) because we have no
deterministic way to replay realistic user dialogue against a running
local stack. This module is that replay: a discovery-driven, scriptable,
concurrent harness that drives the local backend the same way a real
chat user would, then produces hard metrics + an LLM-written semantic
verdict per case.

The driver itself is **never** allowed to call an LLM — every test
input is pre-scripted (`TALK` lines) so a re-run is bit-identical at
the driver layer. All non-determinism is funnelled into the agent's
own LLM, which is exactly the surface we want to observe.

## Where it sits

Lives at `NarraNexus/real_case_e2e_test/` (sibling to `backend/`,
`src/`, `frontend/`). It depends on the running local stack
(`bash run.sh`) and nothing else. The deploy repo's older
`smoke/` scaffold is being retired — anything still wanted from there
should be ported here as cases.

## Reading order

1. `README.md` — the contract for case authors
2. `core/case_spec.py` — the only schema a case author needs
3. `cases/chat/01_single_turn_greeting.py` — minimum viable case
4. `core/runner.py` — orchestration end to end
5. `core/programmatic.py` + `prompts/semantic_per_case.md` — analysis

## Iron-rule alignment

- #1 — all code is English; only test inputs (TALK content) carry
  Chinese
- #7 — does not touch run.sh / Tauri; depends on them, doesn't redefine
- #10 — every new `.py` ships with this mirror tree; per-file md sits
  next to this overview
- #15 — semantic phase explicitly refuses to propose code changes; it
  reviews the run, it does not "fix the LLM"
- #18 — no half-built shortcuts: programmatic + semantic + report +
  cleanup all wired from day one
