---
code_file: src/xyz_agent_context/module/job_module/_job_scheduling.py
last_verified: 2026-04-10
---

# _job_scheduling.py — Job 下次执行时间计算

## 为什么存在

从 `job_repository.py` 分离出来（2026-03-06），把"下次执行时间应该是什么"这条业务规则独立维护。之所以放在 Module 层而不是 Repository 层，是因为这是调度规则（业务逻辑），不是数据访问模式。Repository 不应该包含 cron 解析这类领域知识。

只有一个公开函数：`calculate_next_run_time(job_type, trigger_config, last_run_time)`，对应四种触发模式——ONE_OFF 的固定时刻、SCHEDULED 的 cron 表达式、SCHEDULED 的固定间隔，以及 ONGOING 的固定间隔。

## 上下游关系

- **被谁用**：`job_service.JobInstanceService.create_job_with_instance()` 在创建 Job 时调用（通过 `from xyz_agent_context.repository.job_repository import calculate_next_run_time` 的旧路径，注意这里存在历史遗留的导入路径）；`_job_lifecycle.handle_job_execution_result()` 在更新 SCHEDULED Job 的下次执行时间时调用；`job_trigger._finalize_job_execution()` 作为 fallback 也会调用
- **依赖谁**：`croniter`（可选依赖，用于解析 cron 表达式）；`xyz_agent_context.utils.utc_now`；`schema.job_schema.JobType` 和 `TriggerConfig`

## 设计决策

**`croniter` 是软依赖**：如果没有安装 `croniter` 包，cron 类型的 SCHEDULED Job 不会崩溃，而是 fallback 到"当前时间 + 1 小时"执行，并打印 warning。这意味着 cron 调度静默降级，而不是在 Job 创建时报错。如果需要强约束 croniter 必须存在，应该在 `pyproject.toml` 里加为必须依赖。

**`last_run_time` 的含义**：对于 SCHEDULED 和 ONGOING 的 interval 模式，`last_run_time or utc_now()` 作为基准时间。这里的设计意图是：第一次执行时（`last_run_time=None`）从"现在"开始计算间隔，而不是从 Job 创建时刻。如果 Job 在 00:00 创建但 00:30 才首次执行，下次执行是 01:30 而不是 01:00。

## Gotcha / 边界情况

**cron 表达式不感知用户时区**：`calculate_next_run_time()` 本身不处理时区——它接受一个 `last_run_time`（UTC datetime），用 `croniter` 计算出下一个 UTC 时间。时区转换的责任在 `job_trigger.py` 里：`JobTrigger._execute_job()` 先获取用户时区，在传给 `build_execution_prompt()` 时做格式化，但 `calculate_next_run_time()` 本身不做时区转换。如果用户期望"每天早上 8 点本地时间执行"，需要在创建 Job 时把 cron 表达式转换成 UTC 的对应值，或者在这里补充时区偏移逻辑。

## 新人易踩的坑

- `job_service.py` 里通过 `from xyz_agent_context.repository.job_repository import calculate_next_run_time` 导入这个函数——注意这是旧的导入路径（分离前函数在 repository 里），是遗留代码未清理的痕迹。如果你找不到这个函数，去 `_job_scheduling.py` 里找，而不是 `job_repository.py`。
