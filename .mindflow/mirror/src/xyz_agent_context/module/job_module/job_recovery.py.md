---
code_file: src/xyz_agent_context/module/job_module/job_recovery.py
last_verified: 2026-06-01
stub: false
---

# Intent

Edge-triggered recovery of a single user's PAUSED_NO_QUOTA jobs. PAUSED_NO_QUOTA
is EVENT-recovered, not time-recovered: the blocker (no usable provider) only
clears when the user/admin acts (top up quota, configure a provider, disable the
free-tier toggle, log in). Polling for it is wasted work — and high-frequency
polling was the 2026-05-31 oscillation amplifier. So the backend routes that
perform those mutations call into here after committing.

## User pause / resume core

`pause_job(job_id, db)` and `resume_job(job_id, db)` are the portable
state-transition core for user-initiated pause/resume, called by the authed
dashboard route (`/api/dashboard/jobs/{id}/pause|resume`). They replace that
route's old raw `UPDATE … datetime('now')` SQL, which was SQLite-only (broken on
prod MySQL) and only handled `paused`. `pause` → PAUSED (paused_reason=user;
excluded from due-poll AND auto-resume/cooling re-arm). `resume` accepts PAUSED /
PAUSED_NO_QUOTA / COOLING / BLOCKED_FAILED → recompute next_run, clear backoff
state, flip to ACTIVE. The auth/ownership check stays in the route; the core is
pure DB so it's unit-testable without a request.

## Two entry points (no-quota recovery)

- `rearm_user_no_quota_jobs(user_id, db)` — the awaitable core: find the user's
  PAUSED_NO_QUOTA jobs (matching both `user_id` and `related_entity_id`, since a
  change for a user should revive jobs that run *as* them), run
  `ProviderReadiness.validate` (live), and flip them to ACTIVE + recompute
  next_run ONLY if ready. Best-effort: never raises into the caller. Tested.
- `schedule_user_no_quota_rearm(user_id)` — fire-and-forget wrapper the routes
  call. Non-blocking so it never adds latency to the user's request (login
  returns immediately; the jobs poller picks up the revived jobs next cycle).
  Keeps a task reference so the background task isn't GC'd mid-run (incident
  lesson #2). Uses the global db client (the task outlives the request).

## Cross-process note

Mutations happen in the backend process; the JobTrigger poller runs in the jobs
process. This works without RPC because `job.status` in the DB is the single
authority — the route writes the re-armed status, the poller reads it. JobTrigger
keeps a low-frequency `_resume_eligible_no_quota_jobs` scan as a backstop for
missed edge signals. Design: `2026-06-01-job-scheduler-resilience-design.md`.
