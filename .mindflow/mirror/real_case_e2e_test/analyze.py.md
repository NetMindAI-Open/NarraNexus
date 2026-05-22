---
code_file: real_case_e2e_test/analyze.py
last_verified: 2026-05-13
stub: false
---

# analyze.py — re-run the semantic phase on a saved run directory

## Why it exists

The semantic phase is best-effort: when the first run skipped it
(`--skip-semantic`, missing `claude` CLI, semantic timeout) we still
want to add verdicts later. analyze.py reads an existing
`reports/<ts>/` and produces the missing `semantic/*.md` files in
place, without re-driving the agents.

## Decisions

- Reconstructs the talk script from each transcript rather than
  re-importing the case module. This way analyze.py can run against a
  saved run even after the case file has been edited or deleted —
  the saved transcript is the canonical record.
- Does not touch the manifest. The manifest's `binary_pass` stays the
  programmatic verdict; the semantic markdown is supplementary.
