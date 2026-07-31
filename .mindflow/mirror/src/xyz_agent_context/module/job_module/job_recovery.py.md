---
code_file: src/xyz_agent_context/module/job_module/job_recovery.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — reschedule_job (edit execution time)

`reschedule_job(job_id, new_fields, db)` is the portable core behind the
"修改执行时间" feature, called by `PUT /api/dashboard/jobs/{id}/schedule`. It
sits beside pause/resume as the third user-initiated lifecycle mutation. It
merges the caller's time fields (`run_at` / `cron` / `interval_seconds` /
`timezone`) into the job's existing `trigger_config`, revalidates through
`TriggerConfig` (reusing its naive-run_at / IANA-tz / tz-required / interval
validators), recomputes `next_run` via `compute_next_run`, then persists the new
trigger_config followed by next_run — 两次写入(`update_job_fields` 再 `update_next_run`,
后者是 α+β 强制专用方法),**不是单事务原子**。Editable set = anything EXCEPT
`_NON_EDITABLE_STATUSES` (running + the three terminals); a `paused` job stays
paused (its later resume re-derives next_run anyway). A guard rejects clearing
the last fireable field for the job's type so a job can't silently go dark.
Unit + route tests: `tests/backend/test_job_reschedule.py`.

**cron ↔ interval 互斥（Tier 1 类型切换）**: cron 和 interval_seconds 是
scheduled/ongoing 的两种互斥触发方式。切换时(如 interval→cron)必须清掉另一个,
否则脏字段残留、且 compute_next_run 偏好 cron 会静默屏蔽残留的 interval。
`reschedule_job` 在 merge 后：设了 cron 就把 interval_seconds 置 None,反之亦然。
前端弹窗对非 one_off 任务提供「间隔 / Cron」模式切换,只回传新模式那个字段。
one_off↔scheduled↔ongoing 的真正 job_type 互转不在此范围(牵出 run_at↔周期数据
缺口、end_condition 无法代填、终态复活,需单独设计)。

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
