---
code_file: real_case_e2e_test/core/fixtures.py
last_verified: 2026-05-13
stub: false
---

# fixtures.py — one-liners that hand back resources + own teardown

## Why it exists

Cases should look like dialogue scripts, not infrastructure code. The
fixture surface (`make_user`, `make_agent`) is the only thing a case
author touches when bringing up state, and every created resource is
recorded on a `ResourceLedger` so the runner can clean it up after the
case finishes regardless of pass / fail / exception.

## Decisions

- Cases **cannot delete**. There is no `delete_*` on the fixtures
  surface. Cleanup belongs to the runner so cases stay short and the
  cleanup path is exercised on every run.
- Resource ids are prefixed with `e2e_<run_ts>_<case_id>_*`. A run
  that crashes before cleanup is recoverable: the next runner startup
  sweeps anything matching that prefix.
- No DELETE /users endpoint today; leftover users accumulate. This is
  flagged in cleanup failures and the README; the fix is either
  adding the endpoint or scheduling a periodic prefix-sweep job.
