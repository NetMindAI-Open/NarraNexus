---
code_file: real_case_e2e_test/core/case_spec.py
last_verified: 2026-05-13
stub: false
---

# case_spec.py — the only schema case authors need

## Why it exists

Every case file declares `SPEC: CaseSpec` and `TALK: list[TalkLine]`
at module scope. The runner introspects these to filter, group, and
report. Cases never touch any other type in this module's public
surface — fixtures + drive_turn are the only verbs.

## Decisions

- Frozen dataclasses so discovery is side-effect-free; importing a
  case file should never start anything.
- `case_id` is the file-system key downstream. Renaming it after a
  case has history breaks the trend story for that case, so the
  README points at this explicitly.
- `severity` carries the same vocabulary as the Lark Base bug list so
  reports can be filtered to "everything tied to a P0 bug".
- `semantic_intent` is a free-form sentence that the semantic prompt
  surfaces to Claude; this lets the verdict reference *what should
  pass look like*, not just *what happened*.
