---
code_file: real_case_e2e_test/core/transcript.py
last_verified: 2026-05-13
stub: false
---

# transcript.py — per-case structured record, dumped to JSON

## Why it exists

Downstream phases (programmatic, semantic, report) all read the same
Transcript JSON. Keeping the format stable across versions means trend
tooling can compare runs from different commits.

## Decisions

- We **do not** materialise backend log lines inside the Transcript.
  They live in a parallel `backend_log/<case>.txt` file. Reasoning:
  log slices can be large; keeping them out of the Transcript bounds
  the JSON we ship to Claude in one prompt.
- `final_reply` is recomputed lazily from the event list — never
  stored — so editing the protocol won't drift the cached value.
- `events` is preserved in full (no summarisation) so post-hoc tools
  can re-derive any metric without re-running the case.
