---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/run_once.py
last_verified: 2026-09-04
stub: false
---

# job_module/run_once.py — run one job now

## Intent

Moved from `backend/routes/manyfold/sync.py` (batch 3c.6): the Manyfold alarm needs JobTrigger's private execution body (CAS pickup, prompt, run, finalize) plus the maintenance passes the poller would otherwise do (COOLING re-arm, PAUSED_NO_QUOTA backstop) and a bounded drain of other due jobs. That is builtin.job's business; the route only streams the `JobRunOutcome`. Never raises. Constants and drain semantics are unchanged from the route.
