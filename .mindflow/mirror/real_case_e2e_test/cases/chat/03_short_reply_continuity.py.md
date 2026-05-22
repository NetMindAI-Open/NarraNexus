---
code_file: real_case_e2e_test/cases/chat/03_short_reply_continuity.py
last_verified: 2026-05-13
stub: false
---

# 03_short_reply_continuity — Lark bug #7 reproduction

## Why it exists

Bug #7 in the Lark Base reports that a one-character affirmation ("好")
after an options prompt mis-matches in narrative.continuity_detect and
the next reply falls back to a default narrative. This case is the
deterministic version of that user report: two scripted turns, single
character second turn, then semantic phase decides whether the agent
stayed on topic.

## Decisions

- No `expect_contains` on turn two because the literal contents are
  hard to predict; we rely on the semantic phase + the no-response
  placeholder gate.
- P0 severity matches the original bug priority. If this case stays
  green for a stretch we have stronger evidence the narrative path
  has actually been fixed, not just that the symptom moved.
