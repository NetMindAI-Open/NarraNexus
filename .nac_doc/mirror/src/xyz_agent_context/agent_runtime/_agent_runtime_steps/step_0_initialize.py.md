---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_0_initialize.py
last_verified: 2026-04-10
stub: false
---
# step_0_initialize.py — 流水线第 0 步：初始化

## 为什么存在

在 agent 执行的任何业务逻辑开始之前，必须完成以下准备工作：确认 agent 存在、初始化 ModuleService（加载模块注册表）、创建 Event 记录（作为本次对话的持久化载体）、获取或创建 Session（维持会话连续性）、加载 Awareness（agent 自我认知内容，后续步骤需要）。这些准备工作彼此独立但都是前置条件，集中在 Step 0 执行可以快速 fail-fast。

## 上下游关系

输入：`RunContext`（已有 `agent_id`、`user_id`、`input_content`）、`db_client`、`EventService`、`SessionService`。

输出到 RunContext：`ctx.agent_data`（agent 基本信息 dict）、`ctx.module_service`（ModuleService 实例）、`ctx.event`（新创建的 Event 对象，后续步骤都通过它持久化）、`ctx.session`（Session 对象，用于 Narrative 连续性判断）、`ctx.awareness`（awareness 文本）。

产出 ProgressMessage：运行中（Running）和完成（Completed）两条，前端 sidebar 展示 Step 0 进度。

下游：Step 1 依赖 `ctx.session`（上一次查询信息用于 Narrative 连续性检测）和 `ctx.awareness`（传给 Narrative 选择，提供 agent 上下文）。Step 2 依赖 `ctx.module_service`（调用 `load_modules()`）。

## 设计决策

**Awareness 读两次**：Step 0.5 从 `instance_awareness` 表预加载 awareness 存入 `ctx.awareness`，但 `AwarenessModule.hook_data_gathering()` 在 Step 3 的数据收集阶段会再次从数据库读。这是已知的重复读，注释里有 TODO，说明优化方向是把 `ctx.awareness` 传给 ContextRuntime 来避免二次读取。目前代价可接受（读一次 DB）。

**ModuleService 在 Step 0 初始化而非 AgentRuntime 构造时**：因为 ModuleService 需要 `agent_id` 和 `user_id`，这两个值只在 `run()` 时才有。如果在构造函数里初始化需要额外参数，破坏了 AgentRuntime 作为无状态 orchestrator 的设计。

## Gotcha / 边界情况

- Agent 不存在时直接 `raise ValueError`，这会被 `agent_runtime.py` 的 run() 异常传播到 WebSocket handler。caller 需要妥善处理（通常有全局异常捕获）。
- Awareness 加载失败（try/except）静默降级为空字符串，不中断流水线。如果 awareness 数据对 agent 行为很关键，空 awareness 会让 agent 行为偏离预期，但不会崩溃。

## 新人易踩的坑

- `ctx.awareness` 和 `AwarenessModule` 里读到的 awareness 内容理论上相同，但前者在 Step 0 读，后者在 Step 3 hook_data_gathering 时读。如果中间有 awareness 更新（极罕见），两者可能不一致。
- Step 0 产出的 `ctx.event` 是"已持久化到数据库的新 Event"，但 `final_output` 还是空的，Step 4 里才会 `update_event_in_db` 填入最终输出。不要在 Step 4 之前就认为 Event 包含完整数据。
