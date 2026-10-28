---
code_file: src/narranexus/platform/utils/job_scheduling.py
last_verified: 2026-09-04
---

# job_scheduling.py — Job 下次执行时间计算

## 为什么存在

从 `job_repository.py` 分离出来（2026-03-06），把"下次执行时间应该是什么"这条业务规则独立维护。之所以放在 Module 层而不是 Repository 层，是因为这是调度规则（业务逻辑），不是数据访问模式。Repository 不应该包含 cron 解析这类领域知识。

## 两个公开函数（v2 时区协议之后）

**`compute_next_run(job_type, trigger_config, last_run_utc=None) -> Optional[NextRunTuple]`（v2 新增，推荐用）**

单一真相源：从 `TriggerConfig` 生出一个 `NextRunTuple(local, tz, utc)` 三元组。
- `local`：naive ISO 8601 字符串（用户本地时间视角）
- `tz`：IANA 字符串（如 `Asia/Shanghai`）
- `utc`：aware UTC `datetime`（给 poller 比较用）

`local` 和 `utc` 表达**同一个物理瞬间的两种坐标**，不是独立信息。调用方要么把这三个都原子写进 `instance_jobs`（α+β 字段），要么完全不写——禁止拆开单独更新。

**`calculate_next_run_time(job_type, trigger_config, last_run_time=None) -> Optional[datetime]`（legacy，Task 10 会删）**

旧函数。只返回 naive UTC datetime，不处理时区。Task 10 会一并清掉 `repository/job_repository.py:1331` 的 re-export shim。

## 上下游关系

- **被谁用（目标状态）**：`job_service.JobInstanceService.create_job_with_instance()`、`job_trigger.JobTrigger._execute_*` 里的 post-execution reschedule、`instance_sync_service.create_jobs_for_instances()`——全部走 `compute_next_run`
- **依赖谁**：`croniter`（现已 required）、`zoneinfo.ZoneInfo`、`narranexus.platform.utils.timezone.utc_now`、`schema.job_schema.JobType` / `TriggerConfig`

## 设计决策

**一次算清三种视角**：以前设计是"只返回 UTC datetime，展示时再由消费方转换"。实践发现消费方漏做转换或各自 ad-hoc 做导致 bug——于是改为"一次算清 local + tz + utc，消费方只读不算"。

**cron 处理方式**：`croniter` 拿 **naive 本地时间**作为 base_time（`base_utc.astimezone(zi).replace(tzinfo=None)`），步进出的 naive 结果再 `.replace(tzinfo=zi)` 变回 aware，再 `.astimezone(UTC)` 得到 α。这样 DST 过渡正确（因为 zoneinfo 知道何时从 EDT 切 EST，会给 naive 8:00 配上正确的 offset）。不能把 aware 传给 croniter——croniter 对 aware 的支持历史版本行为不一致。

**`last_run_utc` 的含义**：对于 SCHEDULED / ONGOING 的 interval 模式，`last_run_utc or utc_now()` 作为基准时间。第一次执行（`last_run_utc=None`）从"现在"起算，不是从 Job 创建时刻起算——符合"下次执行时间 = 基准 + 间隔"的直觉。

## Gotcha / 边界情况

**`NextRunTuple` 的 α 与 β 必须原子写**：由调用方（Repository.update_next_run / create_job）保证同时更新 `next_run_time`（α UTC）、`next_run_at_local`、`next_run_tz`（β）三列。任何一个漏更新就会出现"显示一个时间但 poller 按另一个时间触发"的幽灵 bug。

**`timezone is None` 是 bug 不是正常路径**：`compute_next_run` 里显式 `raise ValueError` 兜底，但这应该已经被 `TriggerConfig` 的 validator 挡住。出现 ValueError 说明上游漏校验，而不是用户输入问题。

**`ONE_OFF` 的 post-fire 处理在调用方**：`compute_next_run(ONE_OFF, ...)` 总是返回 `run_at` 的 tuple（因为这是"从 trigger_config 算下次理论触发时刻"的纯函数），不负责判断"这个 job 已经触发过了"。调用方（`job_trigger`）在触发完 ONE_OFF 后要主动 `clear_next_run`。

## 新人易踩的坑

- 不要再用 `calculate_next_run_time`——它是 Task 10 会删的 legacy。新代码一律 `compute_next_run`。
- `croniter` 在 Python 3.13 + zoneinfo 组合下，对 aware datetime 的行为不稳定。本函数采用 "naive in, naive out, 调用方补 tzinfo" 的策略规避。
- `NextRunTuple.local` 是**naive ISO 8601**（如 `2026-05-01T08:00:00`），不带 offset 后缀。展示层如果想显示 `+08:00`，自己拼；但一般直接显示 `local + " " + tz` 更清楚。

## 2026-08-19 — past_schedule_horizon

纯谓词：`(trigger_config, next_fire_utc) -> bool`，判定 recurring job 的下
次 fire 是否越过 `trigger_config.end_at` 地平线（end_at 按 run_at 同款
naive+timezone 约定换算成 UTC 再比较）。无 config / 无 end_at → False，
存量 job 零影响。唯一调用方是 job_trigger 的 SCHEDULED finalize 分支；
放在本文件是因为它是调度规则不是数据访问，与 compute_next_run 同层。

## 2026-09-04 · moved to utils (batch 3c.5)

Moved from `module/job_module/_job_scheduling.py`: the repository (`job_repository`) and `services/instance_sync_service` — platform code — import it, and the platform must not import a builtin plugin. Same functions, same tests (`tests/job_module/test_compute_next_run.py` etc. now import from here); the job module is one more consumer.
