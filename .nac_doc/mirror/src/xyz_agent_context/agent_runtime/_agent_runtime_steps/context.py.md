---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/context.py
last_verified: 2026-04-10
stub: false
---
# context.py — AgentRuntime 执行流水线的共享状态容器

## 为什么存在

`AgentRuntime.run()` 被拆分为 8 个 step 函数，如果用函数参数传递状态，每个函数的参数列表会很长，而且随着功能增加参数会无限膨胀，同时函数间的数据依赖也难以追踪。`RunContext` 是一个 dataclass，在 `run()` 入口创建后传递给所有 step 函数，充当流水线各阶段的共享黑板（blackboard）。

## 上下游关系

在 `agent_runtime.py` 的 `run()` 方法中创建，包含输入参数（`agent_id`、`user_id`、`input_content` 等）和随后各步骤填充的输出字段（`event`、`narrative_list`、`load_result`、`execution_result` 等）。

每个 step 函数接收 `RunContext` 作为参数，读取之前步骤填入的字段，并将本步骤的输出写回。这是一个显式的 mutable shared state 模式。

`RunContext` 不直接依赖任何服务类（EventService 等），服务由 `agent_runtime.py` 创建后以参数形式传给 step 函数，避免 ctx 和服务之间的循环引用。

## 设计决策

**dataclass 而非 dict**：使用 dataclass 而非普通 dict，让 IDE 能做类型推断，step 函数中的 `ctx.main_narrative` 等访问有类型提示。TYPE_CHECKING 保护的 import 避免了运行时的循环依赖。

**`main_narrative` 和 `active_instances` 是计算属性**：`main_narrative = narrative_list[0] if narrative_list else None`，`active_instances = load_result.active_instances if load_result else []`，而不是独立字段。这避免了多个字段间的不一致。

**`previous_instances` 在 Step 1.5 deep copy**：在模块决策（Step 2）改变 `active_instances` 之前，先保存一份快照用于 trajectory 对比，需要 deepcopy 避免引用共享。

**`evermemos_memories` 和 `trigger_extra_data`** 是 Phase 2 功能和 trigger 层数据的透传字段，从 Step 1 填入，到 Step 3 的 `ContextRuntime.run()` 使用。

## Gotcha / 边界情况

- `substeps_*` 字段（`substeps_0`、`substeps_1` 等）是列表，用于收集 ProgressMessage 的子步骤文本。Step 函数直接 `ctx.substeps_0.append(...)` 修改，不是不可变的。
- `__post_init__` 把 `pass_mcp_urls` 内容合并到 `mcp_urls` 里。后续 Step 3.3 会再次更新 `mcp_urls`（加入 ContextRuntime 构建的 MCP URLs），所以 `mcp_urls` 最终包含两个来源的 URL。

## 新人易踩的坑

- `ctx.execution_result` 在 Step 3 完成后才有值，Step 4 和 Step 5 读它时如果 Step 3 抛出异常，这个字段是 `None`，`step_4_persist_results` 有 `if not execution_result: return` 的保护。
- `ctx.module_list` 在 Step 2 中追加了 `MemoryModule`（不通过 Instance 机制管理的 agent 级模块），但 `ctx.active_instances` 里没有 MemoryModule 对应的 instance。两个列表的长度和内容不对应。
