---
code_file: real_case_e2e_test/core/preflight.py
last_verified: 2026-05-13
stub: false
---

# preflight.py — refuse to start when the environment is wrong

## Why it exists

Test failures are noisy enough; the worst kind is "every case red
because the user forgot `bash run.sh`". Preflight pulls those
environmental causes out of the per-case failure path and into a
single, easily-readable refusal at startup.

## Decisions

- Hard refusals: stack health (no `/health` 200), missing `claude` CLI
  when semantic is required.
- Warnings (do not block): provider not configured (caller might be
  exercising error paths intentionally).
- Returns a structured result rather than throwing; the runner formats
  the messages itself so they show up in a recognisable place.
