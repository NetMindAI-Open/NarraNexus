---
code_file: real_case_e2e_test/core/runner.py
last_verified: 2026-05-13
stub: false
---

# runner.py — discovery → group execution → analysis → report

## Why it exists

The orchestrator. Holds the single linear path every run takes:
preflight → discover → for each pillar { gather group with bounded
concurrency, cleanup } → programmatic per case → semantic per case
→ manifest + report + history line.

## Decisions

- Groups are by `SPEC.pillar`, not by an arbitrary chunk size. Cases
  in the same pillar are likely to share the same LLM provider
  rotation; pillars run with a sleep between them so a single
  provider can drain rate-limit headers before the next pillar
  starts.
- Concurrency is **inside** a pillar, bounded by a semaphore. Once
  one pillar finishes, the next one starts cleanly.
- Cleanup is owned by the runner via `cleanup_ledger`. Case
  bodies never call delete_*; they call `ctx.fixtures.*` and rely
  on the ledger.
- Semantic phase is serialised on purpose. Parallel claude
  invocations from one run would spike local CPU and produce verdicts
  in nondeterministic order; the small extra wall clock is worth a
  stable report.
- Manifest + report.md + history.jsonl are all written even when the
  semantic phase failed wholesale, so a partial run is still useful.
