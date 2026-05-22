---
code_file: real_case_e2e_test/cases/chat/02_multi_turn_introduction.py
last_verified: 2026-05-13
stub: false
---

# 02_multi_turn_introduction — context retention across two turns

## Why it exists

The cheapest possible probe of "does the agent's chat history actually
flow into turn N+1". Turn one introduces a name; turn two asks for it
back. If the second reply does not contain "小航" the
`expect_contains` programmatic gate fails — no LLM judgement needed
to flag the regression.

## Decisions

- Hard string assertion (`expect_contains=["小航"]`) on turn two. Agent
  may rephrase but must include the name. Cheaper than asking the
  semantic phase.
- P1 not P0: a regression here doesn't necessarily mean prod broke,
  but it tells us short-term memory is misbehaving — which is a
  precursor to the P0 narrative-fallback bug.
