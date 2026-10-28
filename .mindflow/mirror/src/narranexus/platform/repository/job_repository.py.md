---
code_file: src/narranexus/platform/repository/job_repository.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（review r3 I1）— suspend 只暂停可调度状态（白名单）

`pause_jobs_for_execution_principal` 的状态条件从「`NOT IN` 终态 + paused」黑名单改为
`status IN SUSPENDABLE_JOB_STATUSES`（[[job_schema]]：PENDING / ACTIVE / COOLING）。原因：reinstate 把
suspend 暂停的每条 job 一律恢复成 ACTIVE（`resume_job`，next_run=从现在起算），这只对这三种正确；
BLOCKED / BLOCKED_FAILED 若被压成 paused，解封后会以 ACTIVE 提前开跑（依赖输出尚不存在）；RUNNING
由在飞 run 的 finalize 负责改写；PAUSED_NO_QUOTA / PAUSED_SPEND_CAP 保留自己的 reason。这些行本来就
不会被 `get_due_jobs` 捞走，之后若变成到期，由 [[job_trigger]] 的入队门拦下。
`get_jobs_paused_for_execution_principal` 不变（执行主体谓词与 pause 逐字相同，状态钉 `paused`+reason）。
锁：`test_cooling_jobs_are_paused`、`test_non_schedulable_statuses_are_left_as_they_are`（参数化 5 个状态）、
`test_suspend_then_reinstate_read_is_the_same_population`（加 blocked/blocked_failed）+ `_mysql` twin 同步。

## 2026-09-10（review r2 I-B + M-b）— reinstate 的读半边 + pause 不再改写已暂停 job 的 reason

- 新 `get_jobs_paused_for_execution_principal(user_id, paused_reasons)`：
  `WHERE (与 pause 逐字相同的执行主体谓词) AND status = 'paused' AND paused_reason IN (...) ORDER BY id`，
  无行数上限（调用方要恢复的是整批）。status 与 reason **同时钉住**：用户自停（`'user'`）、NULL / 空串
  reason、quota/spend 暂停、其它执行主体都不会被选中；`paused_reasons` 为空直接返回 `[]`。供
  [[job_recovery]] 的 `resume_jobs_paused_for_principal`（admin reinstate）使用。
- `pause_jobs_for_execution_principal` 的排除条件从「终态 + 已因同一 reason 暂停（COALESCE）」改为
  「终态 + **任何** `paused`」：r1 会把用户自停/NULL reason 的 job 改写成 `banned`，配上 reinstate 的
  reason 过滤就等于解封时把用户自己的暂停一并恢复。COALESCE 分支随之删除。
- **M-b SQLite 前提**：`paused_at` / `updated_at` 以 `to_datetime6_literal` 写成
  `YYYY-MM-DD HH:MM:SS.ffffff`（空格），而 SQLite 下其它 `updated_at` 写入走 ISO-8601 adapter（带 `T`）；
  同列两种文本形态按字符串排序时 `' ' < 'T'`，所以**仅 SQLite** 上 `get_jobs_by_entity_id` /
  `get_jobs_by_status` 的 `ORDER BY updated_at` 对这些行的相对位置可能偏。MySQL 是真 DATETIME(6)，
  dev/prod 不受影响。
锁：`tests/repository/test_job_repository_pause_principal.py`（`test_already_paused_jobs_keep_their_own_reason`、
`test_reinstate_read_*` 三条、含 `related_entity_id=''`/NULL 与空串/NULL reason）+ `_mysql` twin
（`test_reinstate_read_on_mysql`，pause 断言改为已暂停行保持原 reason）。


## 2026-09-10（review r1 I5）— 四个「活跃 job」读改用 `LIVE_JOB_STATUSES`

`find_active_by_title` / `get_active_jobs_by_narrative` / `get_active_jobs_by_agent` /
`get_active_jobs_summary` 原来各写一份状态字面量列表（两份 `('pending','active')`、两份带
`running`），B-16 之后 BLOCKED 从这四处全部漏掉，COOLING 从来就漏。现在四处都拼
`_LIVE_STATUS_SQL`（`status IN (%s, …)`，占位符数量随 [[job_schema]] 的 `LIVE_JOB_STATUSES`
生成）+ `_LIVE_STATUS_PARAMS`——集合只在 schema 定义一次，SQL 里不再有裸字面量。
`get_due_jobs` / `try_acquire_job` / `update_next_run_time_by_instance` 的状态集合**不**改：
到期扫描只认 PENDING/ACTIVE 是 B-16 的修复本体。
锁：`tests/repository/test_job_repository_live_statuses.py`（每个 live 状态可见、每个非 live 状态
不可见、四个读结果集一致、`get_due_jobs` 不受影响）+ `_mysql` twin（含 `user_clause` 两种分支）。

## 2026-09-10（review r1 I2/I3）— `pause_jobs_for_execution_principal`：一条 UPDATE，按执行主体选行

admin suspend（[[suspend]]）原来 `get_jobs_by_user(user_id, limit=500)` 再逐条 `update_job`：
(a) 只按 owner 选行，与 [[job_trigger]] 的 `exec_uid = related_entity_id or user_id` 口径相反——
以被封号者身份执行、owner 正常的 job 不会被暂停，owner 被封、执行主体正常的 job 反而被标
`banned`，两个组件对同一条 job 判断相反；(b) 静默截断 500（刷 job 的马甲正是超 500 的那种）
且 N 次往返。新方法一条语句：
`WHERE (related_entity_id = ? OR ((related_entity_id IS NULL OR related_entity_id = '') AND user_id = ?))
AND status NOT IN (三终态)` + 「已暂停」排除（r1 为 COALESCE 同 reason 排除，r2 I-B 改为排除任何 `paused`，见顶部），
返回 rowcount。`paused_at` /
`updated_at` 用 `to_datetime6_literal` 字面量传参，两方言都不依赖驱动侧 datetime adapter。
裸 SQL 配双方言：`tests/repository/test_job_repository_pause_principal.py`（含 520 行无上限、
委托执行正反两例）+ `_mysql` twin（谓词 / 字面量 / rowcount）。

## 2026-09-09 — B-16：`create_job` 加 `status` 参数 + BLOCKED 纳入激活集合

两处改动，同一个根因（依赖链「永不触发」，GitHub #114/#109）：

1. `create_job` 新增 `status: JobStatus = JobStatus.PENDING` 参数——此前硬编码
   `JobStatus.PENDING`，即便调用方（[[job_service]] / `instance_sync_service`）
   早就判定这个 job 的 ModuleInstance 该是 BLOCKED，Job 自己的状态也从不跟着变，
   `get_due_jobs()`（只选 PENDING/ACTIVE）照样把它当成到期任务拉走。
2. `update_next_run_time_by_instance`（`JobModule.on_instance_activated` 依赖
   完成后调的那个方法）的 `WHERE status IN (...)` 原来只有 PENDING/ACTIVE——
   docstring 早就写着「Used to activate BLOCKED Jobs」，代码却从没把 BLOCKED
   放进去，是个从一开始就没兑现的方法。现在把 BLOCKED 纳入 WHERE，`SET`
   里显式把 `status` 写成 ACTIVE。

两处必须同时改：只改#1，job 正确落成 BLOCKED 但从此激活不了（0 rows
affected）；只改#2，因为 job 状态从来不是 BLOCKED，这个分支永远轮不到。

## 2026-09-09 — B-15：两处读路径改用 `TriggerConfig.from_stored_dict`

`_row_to_entity`（`get_job`/`find` 等一切读路径的落脚点）和
`recover_stuck_jobs` 的 next_run 重算，此前都用严格构造器
`TriggerConfig(**trigger_config_data)` 重建已存的行。2026-04-21 才上线的
`timezone_required_for_time_bearing_triggers` validator 让这两处对**更早写入**
且缺 `timezone` 的旧行必炸 `ValidationError`——`_row_to_entity` 这处直接让
`get_job()` 抛出，`job_module._load_related_jobs_context` 的宽 `except` 把它
悄悄吞掉，整批相关 job 上下文消失不见；`recover_stuck_jobs` 那处有自己的
try/except，会跳过重算 next_run（该行为不算错，但同样源于同一个洞）。

改用 [[job_schema]] 新增的 `TriggerConfig.from_stored_dict`：只在内存里给缺失
的 `timezone` 补 `"UTC"`，从不回写行本身。**其余** 4 个 `TriggerConfig(**...)`
写路径（job_update 工具、reschedule、创建、instance 自动装配）保持严格——
它们校验的是即将持久化的新数据，必须继续拒绝缺 timezone。

## 2026-08-14 — origin 两列贯通读写

`create_job` 接 `origin_source` / `origin_channel_id`，`_row_to_entity` /
`_entity_to_row` 同步。语义与「为什么是两列」见 [[job_schema]] 与
[[schema_registry]]。

## 2026-08-04 — get_active_jobs_by_agent 增加 user_id 过滤（W1）

dedup 用途的候选查询原来只按 agent 取——一个用户的 job 标题会挡住另一个
用户的创建（相似判重跨用户误伤）。新增可选 `user_id` 参数在 SQL 层过滤
（不是取回后内存过滤：limit 50 会让老 job 把当前用户的候选挤出窗口）。
dedup 调用方（job_service 相似门）必须传；不传 = 旧行为，留给真需要
agent 全量视角的读者。判据必须是 `user_id is not None`，不能用 truthiness：
空字符串虽然是异常边界值，仍代表一个明确 user bucket；把它当「未提供」会
静默退回 agent 全量候选池，重新引入跨用户误伤。

## 2026-07-30 — update_job_fields trigger_config 序列化对齐 mode='json'

`update_job_fields` 的 `trigger_config` 分支原来用 `json.dumps(value.model_dump())`,
对含 `run_at`(datetime)的 TriggerConfig 会抛 "not JSON serializable"——一次性任务改
执行时间时必然触发。改为 `model_dump(mode='json')`(dict 分支加 `default=str`),与
`create_job` / `update_job`(`_entity_to_row`、line 443)本来就用的写法一致。这是潜在
bug 的根因修复,不只是绕过 reschedule 场景。

## 2026-07-13 — 恢复字段进白名单 + 未调度僵尸查询

`update_job_fields` 的 `allowed_fields` 白名单新增 `paused_reason` /
`consecutive_failure_count` / `cooldown_until`——`job_service.update_job` 复活 job
时要能清掉这三个恢复态,不加白名单会被静默过滤(事故 2026-07-13)。

新增 `get_active_scheduled_jobs_missing_next_run()`:查 `status=active 且
next_run_time IS NULL 且 job_type IN (scheduled, ongoing)` 的僵尸 job(ONE_OFF 排除,
它完成后本就没有 next_run)。供 `JobTrigger._heal_unscheduled_active_jobs` 自愈用——
这类 job `get_due_jobs`(`next_run_time <= now`)永远选不到。

## 2026-06-08 — job search index (projection)

`create_job` now also writes a `memory_job` index (title + description + `source_ref`→job) so jobs are findable via `remember`; `update_job_fields` RE-INDEXES when title/description change, keeping the searchable surface fresh. Status and schedule stay live in `instance_jobs` — the index never holds them; the agent fetches current state via `job_retrieval_by_id` through the pointer. Index writes are best-effort.

## 2026-06-01 — resilience fields + find_long_running_jobs (batch ②)

`_row_to_entity` / `_entity_to_row` now carry the four backoff/pause columns
(`consecutive_failure_count`, `cooldown_until`, `paused_reason`, `paused_at`).
New read-only `find_long_running_jobs(threshold_minutes)` returns RUNNING jobs
older than the threshold for DIAGNOSTICS only (铁律 #14 — never force-recover).
It filters in Python, NOT via SQL `started_at < %s`: SQLite stores datetimes
with a 'T' separator but binds a datetime param with a space, so the string
comparison is wrong ('T' > ' '). Native MySQL DATETIME is fine, but Python
filtering is correct on both. (The same latent SQLite bug affects `get_due_jobs`
and `recover_stuck_jobs`, which still use the SQL comparison — out of scope here,
prod is MySQL.)

## 2026-05-27 — defensive None→now() for created_at / updated_at

Real prod incident (Owner dmg, 2026-05-27 18:46): `job_trigger`'s
poll loop crashed every cycle with
  `2 validation errors for JobModel`
  `created_at: Input should be a valid datetime, input_value=None`
  `updated_at: Input should be a valid datetime, input_value=None`
because some pre-existing job rows in the local sqlite DB had
NULL `created_at`/`updated_at` (the columns previously had no NOT
NULL / DEFAULT constraint — see companion fix in [[schema_registry]]).
JobModel itself requires `datetime` (not `Optional[datetime]`), so
the None passed to the pydantic constructor was a hard rejection.

`_row_to_entity` now coerces NULL via `row.get("created_at") or
datetime.now()` at the row→entity boundary. The model stays strict
(no `Optional` ripple through downstream consumers); the DB
columns get NOT NULL + DEFAULT for new INSERTs (in schema_registry);
old NULL rows still load via this fallback. Belt-and-braces — once
all existing NULLs naturally get UPDATEd by future writes the
fallback becomes dead code.

## 2026-05-22 — get_jobs_by_status (no-quota resume, #6)

Added `get_jobs_by_status(status, limit)` so JobTrigger's periodic recheck can
fetch `PAUSED_NO_QUOTA` jobs to consider for resume. No row lock (unlike
`get_due_jobs`' `FOR UPDATE SKIP LOCKED`) — the recheck only flips status;
actual execution still re-acquires via `try_acquire_job` when the job later
fires. `get_due_jobs` filters `status IN (PENDING, ACTIVE)`, so paused jobs
never fire while paused.

# job_repository.py

## Why it exists

`JobRepository` manages the `instance_jobs` table — the persistence layer for the agent's background task system. Its most critical responsibility beyond standard CRUD is `get_due_jobs()`, which fetches jobs that are ready to fire and acquires row-level locks (`SELECT ... FOR UPDATE`) to prevent two JobTrigger processes from claiming the same job simultaneously.

## Upstream / Downstream

`JobTrigger` (background service) calls `get_due_jobs()` on a polling loop and fires each returned job through `AgentRuntime`. `JobModule.after_turn()` calls `update_job()` after execution to record the LLM's `JobExecutionResult` (new status, process entries, next_run_time). The job management API routes in `backend/routes/` call `create_job()`, `get_jobs_by_agent()`, and `delete_job()` to serve the frontend job panel.

## v2 时区协议（2026-04-21）

`instance_jobs` 表现在有 α + β 两套 next/last run 字段：

- α：`next_run_time` / `last_run_time`（UTC aware，物理瞬间）——**仅 poller 的 `get_due_jobs()` 用**
- β：`next_run_at_local` / `next_run_tz` / `last_run_at_local` / `last_run_tz`（用户本地 naive ISO + IANA）——**所有面向 LLM / UI 的读取路径都用这套**

对 α+β 的更新**必须走这三个专用方法**，不能直接拼 SQL 更新：

- `update_next_run(job_id, NextRunTuple)`：原子写 α + β 下次运行
- `update_last_run(job_id, utc, local, tz)`：原子写 α + β 最后运行
- `clear_next_run(job_id)`：one_off 触发完、ongoing 达到终止条件时清空下次运行

违反原子性（只更新 α 不更新 β 或反之）会产生"显示时间和实际触发时间不一致"的幽灵 bug。

## Design decisions

**`id_field = "job_id"`**: unlike `AgentRepository` where id_field is the auto-increment, here `job_id` is both the business key and the effective lookup key. `BaseRepository.get_by_id("job_xxx")` works correctly.

**`get_due_jobs()` uses `SELECT ... FOR UPDATE` (row lock)**: this is the one place in the entire repository layer where a transaction-level lock is acquired. It is necessary because `JobTrigger` may run as multiple processes and they must not double-fire a job. The lock is held for the duration of the status update to `RUNNING`. If the process dies after the lock but before the update, the job stays locked until the transaction times out or the process is restarted.

**`trigger_config` stored as JSON**: `TriggerConfig` is a Pydantic model serialized to a JSON string. The repository deserializes it in `_row_to_entity()` as `TriggerConfig(**json.loads(...))`. This means new optional fields added to `TriggerConfig` (like `end_condition`, `max_iterations` for ONGOING jobs) are backward compatible — old rows simply have `None` for those fields.

**`semantic_search()` uses in-process numpy cosine similarity** — same pattern as `InstanceRepository.vector_search()`. All job embeddings are loaded, deserialized, and compared in Python. No database vector index.

## Gotchas

**`JobModel.limit` field**: this field (default `10`) is present on the `JobModel` schema but its serialization in `_entity_to_row()` needs to be checked — if `limit` is included in the row dict, it will be written to the database as a column. The `instance_jobs` table schema should have a `limit` column or the insert will fail. This looks like a schema design error — `limit` is a pagination hint that should not be on the domain model.

**`process` is a JSON list that grows with each run**: `update_job()` should append to `process`, not overwrite it. If the caller passes a `process` list that only contains the current run's entries (not the cumulative history), older entries will be lost. Always fetch the existing `process` list and append before calling `update_job()`.

## New-joiner traps

- `get_due_jobs()` returns jobs with status `PENDING` (never run) or `ACTIVE` (scheduled, due for next run) where `next_run_time <= now`. Jobs with status `RUNNING` or `COMPLETED` are never returned even if they are overdue — `RUNNING` means another process is executing, `COMPLETED` means done.
- `monitored_job_ids` is used by "monitor job" patterns where one ONGOING job watches the completion of other jobs. If you see a job with a non-empty `monitored_job_ids` list, it is a meta-job that should not execute normally — its trigger logic is driven by the monitored jobs' state changes.
