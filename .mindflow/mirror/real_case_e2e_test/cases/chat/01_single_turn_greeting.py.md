---
code_file: real_case_e2e_test/cases/chat/01_single_turn_greeting.py
last_verified: 2026-05-13
stub: false
---

# 01_single_turn_greeting — minimum viable case

## Why it exists

This is the case the README points at as the canonical template. Its
job is **not** to detect a specific regression — its job is to keep
the harness itself honest: as long as the chat WebSocket path round
trips a single short message, this case is green; if a refactor
breaks the protocol, this is the first thing that goes red.

## Decisions

- 120 s turn timeout: comfortably above narrative + provider startup
  latency on a cold first request, well below an "agent stuck" wall
  clock. If a future provider really needs longer, override per-line.
- `expect_not_contains` on the no-response placeholder so the
  programmatic gate catches the exact symptom of the bug families
  this suite was built to detect, even when the rest of the run looks
  fine on the wire.
- Chinese greeting in TALK matches the platform's primary user
  language. Adding an English-language variant later is a sibling
  file, not an edit to this one.
