---
code_file: real_case_e2e_test/run.py
last_verified: 2026-05-13
stub: false
---

# run.py — CLI entrypoint for the e2e suite

## Why it exists

A thin argv → config → `core.runner.execute` adapter. We keep argument
parsing out of `core.runner` so the runner can be invoked from other
contexts (e.g. a future scheduled job, or a different launcher script
that wants a different default set) without dragging the CLI surface.

## Decisions

- Defaults read from env (`NN_E2E_*`) so a developer's local
  preferences live in their shell, not in the args.
- Three exit codes: 0 (all green), 1 (some case failed the
  programmatic gate), 2 (preflight refused — stack down etc). CI can
  branch on the difference.
- No `argparse subcommands` here; analyze.py is its own entrypoint
  because re-running the semantic phase is a different mental model
  (post-hoc on a saved run, not a live driver).
