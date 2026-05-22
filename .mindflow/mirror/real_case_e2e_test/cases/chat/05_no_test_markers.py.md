---
code_file: real_case_e2e_test/cases/chat/05_no_test_markers.py
last_verified: 2026-05-13
stub: false
---

# 05_no_test_markers — debug strings must not reach users (Lark bug #3)

## Why it exists

Bug #3 reported "Agent sent test messages to Lark" — debug strings
from never-promoted code paths leaked into user-visible output. This
case sends a benign query and uses `expect_not_contains` to fail the
programmatic gate the moment any of a small list of marker strings
shows up in the reply.

## Decisions

- The marker list is intentionally short and English-leaning (`test
  message`, `测试信息`, `stub_reply`). Adding everything plausible
  would create false positives.
- P0 because Bug #3 was P0 and the regression is cheap to catch.
- Single turn keeps wall clock low; this case is meant to ride along
  every run.
