---
code_file: packages/narranexus-contracts/src/narranexus/contracts/job.py
last_verified: 2026-09-04
stub: false
---

# contracts/job.py — JobRunOutcome

## Intent

The run-once outcome shape shared by builtin.job's `jobs.run_once` service and the Manyfold sync route, so the route depends on the contract, not on the job module. The route subclasses it to add the completion text it streams.
