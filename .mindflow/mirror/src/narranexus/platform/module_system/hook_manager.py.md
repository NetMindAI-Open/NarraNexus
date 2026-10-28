---
code_file: src/narranexus/platform/module_system/hook_manager.py
last_verified: 2026-09-04
---

## 2026-08-14 — `_parallel_data_gathering` 的死导入修复

`from .ctx_data_merger import ContextDataMerger` 指向一个不存在的模块（真身在
`_module_impl.ctx_merger`，早年搬家漏扫）——并行采集是默认关闭的旁路，全仓无人传
`parallel_data_gathering=True`，所以这个 ImportError 潜伏至今，被 PR#308 接进 CI 的
pyright（reportMissingImports warning）照出来。修复=改从 `._module_impl` 导入；新增
test_hook_manager_parallel_import.py 真正驱动该分支，防止「文档写着能开、一开就炸」。

## 2026-08-14 — 依赖链回调实例改走 `spawn`

`hook_callback_results` 里触发新激活实例的 `asyncio.create_task` 改为
`utils.background_tasks.spawn`，名为 `callback_instance:{instance_id}`。这个 task
启动的是**一整个新的 AgentRuntime run**，丢一个就是一条依赖链无声停在半路；而下面
「fire-and-forget 风险」那条记的兜底（`ModulePoller`）恰恰看不见这个进程内捷径失败了
——所以至少得让它死有声息。

注意范围：`spawn` 不改变进程退出时任务被取消这一事实，链路的**持久**保证仍然属于
`ModulePoller`，不属于这里。

## 2026-05-20 — `persist_turn` runner (synchronous phase)

Added `persist_turn(module_list, params)` mirroring `after_turn`
but for the SYNCHRONOUS, next-turn-critical persistence phase (see [[base.py]] 2026-05-20).
The runtime awaits it right after Step 4 and before dispatching the background hooks
(see [[agent_runtime.py]] Step 4.6). Parallel `asyncio.gather` across modules; a
single module's failure is logged non-fatally (must never crash the turn — the user
already has their answer). Reuses `build_after_execution_params(ctx)` extracted into
[[step_5_execute_hooks.py]] so both phases build identical params.

# hook_manager.py — Hook 生命周期编排

## 为什么存在

`HookManager` 把"如何跨多个模块调度同名 hook"的并发策略从 `AgentRuntime` 里分离出来。它持有两个关键决策：数据收集阶段是否并行、以及如何把 `after_turn` 返回的回调结果转化为依赖链激活。

## 上下游关系

- **被谁用**：`AgentRuntime`（`agent_runtime/` 目录）在流水线第 3 步（数据收集）和第 6 步（执行后处理）调用；`hook_callback_results()` 还需要 `NarrativeService` 来处理依赖激活
- **依赖谁**：`XYZBaseModule` 列表（通过抽象接口调用）；`asyncio.gather` 用于并行；`ContextDataMerger`（`_module_impl/ctx_merger.py`）用于并行模式下的合并

## 设计决策

**`gather` 默认顺序执行**：`SocialNetworkModule` 在 `gather` 里向 `ctx_data.extra_data` 写入 `related_job_ids`，`JobModule` 在同一阶段读取它——这是一个显式的模块间数据传递约定。如果并行执行，双方拿到的都是原始副本，跨模块数据传递会失效。顺序执行是唯一安全默认值，代价是约 3 个模块 × 100ms = 300ms 的串行时间。

**`after_turn` 始终并行**：执行后的处理（保存对话历史、更新 Job 状态、更新社交图谱）之间互不干扰，可以安全地并行执行，从约 300ms 降至约 100ms。

**`HookCallbackResult` 触发依赖链**：任何模块的 `after_turn` 都可以返回一个 `HookCallbackResult`（`trigger_callback=True`），`hook_callback_results()` 会调用 `NarrativeService.handle_instance_completion()` 检查依赖，并用 `asyncio.create_task` 在后台触发等待中的实例，不阻塞当前轮次。

**单模块失败不中断其他模块**：每个 hook 调用用 try/except 包裹，失败用结构化异常记录后继续。这是有意的——单模块故障不应崩溃整个 Agent 轮次。

## Gotcha / 边界情况

- **并行模式 + `ContextDataMerger` 的 last-write-wins**：并行模式下每个模块拿到 `ctx_data` 的深拷贝，执行后通过 `ContextDataMerger.merge()` 合并。`LIST_FIELDS`（如 `chat_history`）用 extend，`DICT_FIELDS`（如 `extra_data`）用深合并，其他字段非 None 值覆盖。两个模块同时修改同一个非列表字段时，最后一个赢，前者的修改被静默丢弃。
- **`narrative` 可以是 `None`**：当 working_source=JOB 且对应 Narrative 找不到时，`narrative` 参数为 `None`，`hook_callback_results` 会跳过依赖检查并打 warning。这不是 bug，是已知限制。
- **`asyncio.create_task` 的 fire-and-forget 风险**：后台激活的任务在当前事件循环里运行，进程退出时会被取消。生产环境里复杂 Job 链的执行保证依赖 `ModulePoller` 服务（`services/`）而非这里。

## 新人易踩的坑

- 把模块间数据传递依赖（如 `SocialNetworkModule` → `JobModule` 通过 `extra_data`）理解为"模块相互引用"——实际上模块本身不互相 import，依赖通过 `ContextData` 的字段（`extra_data` 字典）传递，顺序执行保证了先写后读。
- 修改 `parallel_data_gathering=True` 而不仔细检查模块间的 `extra_data` 依赖，会导致 `JobModule` 读不到 `SocialNetworkModule` 写入的 `related_job_ids`，症状是 Job 上下文缺失，排查困难。

## 2026-09-04 · stage-named dispatch (batch 5c)

`gather` / `persist_turn` / `after_turn` dispatch to the modules' same-named participations (timing labels unchanged).
