---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/job_recovery.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（review r2 I-B）— `resume_jobs_paused_for_principal`：解封时恢复封号暂停的 job

`resume_jobs_paused_for_principal(db, user_id, paused_reasons) -> int`，经 `jobs.resume_for_principal`
服务（[[services]]）暴露给 `POST /api/admin/reinstate`（[[suspend.py]]）。用
`JobRepository.get_jobs_paused_for_execution_principal` 取「以该用户身份执行、`paused` 且 reason 在给定集合」
的 job——与 suspend 的暂停人群互为逆。每条都走 job 层恢复，绝不盲翻 ACTIVE（封禁期间 `next_run_time`
已成过去时，盲翻会被 `get_due_jobs` 立刻捞走补跑）：先 `compute_next_run(last_run_utc=now)`，周期 job 若
下次触发越过 `end_at`（`past_schedule_horizon`）→ COMPLETED + `clear_next_run` + 实例
`InstanceRepository.update_status(COMPLETED)`，与 [[job_trigger]] 的 `_rearm_cooled_jobs` /
`_resume_spend_capped_jobs` 同规则，不计入返回数；其余调用 `resume_job`（重算 next_run、清
`paused_reason`/`cooldown_until`/失败计数、ACTIVE）。仓库异常直接抛出，best-effort 策略归调用方。
注：用户手动的 `resume_job` 本身仍不做 `end_at` 判定（既有行为，本次未改）。


## 2026-09-10（review r1 I11）— `_TIME_FIELDS` → `_RESCHEDULE_FIELDS`，从 `TriggerConfig.TIME_BEARING_FIELDS` 派生

不再手抄一份时间字段清单：`_RESCHEDULE_FIELDS = TIME_BEARING_FIELDS − {end_at} + {timezone}`。
语义是「reschedule 可编辑的字段」而非「time-bearing 字段」：`timezone` 本身不带时间但随之
一起编辑；`end_at` 是排期的**边界**（"跑到哪天"）不是触发规则，UI 的 `RescheduleBody` 也没有
它的编辑器，刻意排除（[[JobExpandedDetail.tsx]] 的 mirror 早记录了这一点）。行为零变化，只是
清单不再会与 schema 漂移。锁：`test_reschedule_fields_derive_from_the_time_bearing_list`。

## 2026-09-10（review r1 C1）— `PAUSED_SPEND_CAP` 进 `_RESUMABLE_STATUSES`

`resume_job` 接受 `paused_spend_cap`：用户在 Jobs 面板手动恢复被日花费上限暂停的 job。
早恢复无害——下一次调度开始时 [[job_trigger]] 会再判一次 cap，仍超标就再暂停；自动路径
（当地次日 / cap 关闭）是 job_trigger 的 `_resume_spend_capped_jobs`。`_NON_EDITABLE_STATUSES`
的注释顺手把可编辑清单补全（含 blocked / paused_spend_cap）。
锁：`tests/backend/test_job_pause_resume.py::test_resume_paused_spend_cap_job`。

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
