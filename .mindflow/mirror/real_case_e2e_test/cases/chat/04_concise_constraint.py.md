---
code_file: real_case_e2e_test/cases/chat/04_concise_constraint.py
last_verified: 2026-05-13
stub: false
---

# 04_concise_constraint — agent honors explicit length cap

## Why it exists

Catches a class of agent-policy drift: the model "should" obey an
explicit "答 ≤ 20 字" constraint but often ignores it. This is not a
P0 prod regression — it's a quality probe that surfaces when prompt
or model changes silently degrade instruction following.

## Decisions

- `expect_contains=["是"]` is enough as a binary check that the agent
  answered yes; "no" would have failed the question itself. Length
  judgement (the actual point of the case) is left to the semantic
  phase, which is allowed to be lenient.
- P2 — informational regression signal, not a blocker.
