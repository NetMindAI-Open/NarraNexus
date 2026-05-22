---
code_file: real_case_e2e_test/core/programmatic.py
last_verified: 2026-05-13
stub: false
---

# programmatic.py — binary verdict + hard metrics, no LLM

## Why it exists

The runner needs a deterministic pass/fail gate that does not change
between runs of the same transcript. Subjective judgement (did the
agent answer the right question?) belongs to the semantic phase;
objective signals (no-response placeholder present, fatal error
observed, expect_contains missing) belong here.

## Decisions

- The verdict is a short, totally-ordered list of gates. The first
  one that fires wins; the README documents the order so case authors
  can predict the reported reason.
- Per-turn metrics live alongside the case-level rollup so reports
  can drill down without re-parsing the transcript.
- `models_seen` is derived from the backend log slice. Without the
  log it stays an empty list — never inferred from event metadata —
  so a report that lists models can be trusted, and one that doesn't
  is the "log wasn't wired" signal.
- No-response placeholder + fatal error event are explicit binary
  signals because they are the platform's own structured admissions
  that something broke; they outrank "everything else looked fine".
