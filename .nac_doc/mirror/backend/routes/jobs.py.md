---
code_file: backend/routes/jobs.py
last_verified: 2026-04-10
stub: false
---

# routes/jobs.py — Job 管理路由

## 为什么存在

Job 是一种带触发条件的任务（单次、定时、持续），由 `ModulePoller` 在后台轮询执行。这个路由暴露前端需要的 Job 操作接口：查列表、查详情、取消、以及批量创建带依赖关系的 Job 群组（Job Complex）。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(jobs_router, prefix="/api/jobs")`；前端 Jobs 面板
- **依赖谁**：
  - `JobRepository` — Job 的基础查询和状态更新
  - `xyz_agent_context.utils.db_factory.get_db_client` — 直接查询 `instance_jobs` 和 `module_instances` 表
  - `xyz_agent_context.module.job_module.job_service.JobInstanceService` — 创建 Job Complex 时同时创建 ModuleInstance 和 Job 记录

## 设计决策

**Job 依赖关系存在 `module_instances` 而非 `instance_jobs`**

依赖关系（`depends_on`）存储在 `module_instances.dependencies` 字段里，而不是 `instance_jobs` 表。列表查询时需要先拿到所有 job 的 `instance_id`，再批量查 `module_instances` 表，把依赖关系附加到 job 响应里。这是因为 Job 和 Module Instance 是 1:1 对应的，依赖是实例级别的概念，不是 job 级别的。

**Job Complex 的依赖解析**

创建 Job Complex 时，`task_key` 是用户用来表达依赖关系的临时标识，最终要转换成实际的 `job_id`。转换是顺序的：按 `request.jobs` 的顺序逐一创建，每创建一个就把 `task_key -> job_id` 记录下来，下一个 job 的依赖解析就能用到之前的映射。这意味着 `request.jobs` 的顺序必须是拓扑序（被依赖的 job 先出现）；否则解析时找不到 `task_key`，会报 "Invalid dependency" 错误。

实际上代码里会先校验所有 `task_key` 存在，但不做拓扑排序验证。如果 job A 依赖 job B，但 B 在请求列表里排在 A 后面，创建 B 时就能找到 A 的 job_id，但创建 A 时找不到 B 的 job_id——因为 B 还没创建。调用方必须自己保证顺序。

**`job_row_to_response` 的递归 JSON 解析**

`trigger_config` 和 `process` 字段可能被双重 JSON 序列化，代码里用递归函数 `parse_json_recursive` 反复 `json.loads` 直到得到期望的类型。这是数据写入时格式不一致的历史遗留问题。

## Gotcha / 边界情况

- **取消 running 状态的 Job**：处于 `running` 状态的 Job 不能被中断（Agent 正在执行中），但可以被标记为 `cancelled`，标记后 ModulePoller 不会再重新调度这个 Job。当前执行不会停止。
- **`status` 过滤的白名单**：列表接口对 `status` 参数有硬编码的有效值列表 `["pending", "active", "running", "completed", "failed", "blocked", "cancelled"]`。如果核心包里 `JobStatus` 枚举新增了状态值，这里的白名单需要同步更新，否则过滤会报 "Invalid status" 错误。
- **`format_for_api` 确保 UTC 时间格式**：`next_run_time` 等时间字段都通过 `format_for_api` 转换为带 `Z` 后缀的 ISO 8601 格式，以确保前端 `new Date()` 能正确识别为 UTC。

## 新人易踩的坑

创建 Job Complex 时如果某个 job 创建失败，已经创建的 job 不会回滚。API 返回 `success=False` 和错误信息，但系统里已经存在部分创建的 job 群组。调用方需要自行处理清理逻辑。
