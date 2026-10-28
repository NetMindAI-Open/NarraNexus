---
code_file: src/narranexus/platform/agent_runtime/_agent_runtime_steps/context.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— `assembled` / `run_id`

Assemble 阶段输出（ContextRuntimeOutput）与本次运行的 trace id（钩子收到）。

## 2026-08-21 — `no_durable_topic` 字段:路由判决→持久化冻结的跨层契约(④-A′)

新字段 `no_durable_topic: bool = False`,由 step_1 从 `selection_result` 抄入,
**唯一消费者是 step_4:523** —— 为真时该轮跳过 updater(叙事的
name/summary/keywords 不因寒暄被改写),事件本身照常落库。三条语义必须一起记:

1. **按轮判定,不是按线** —— 同一条线的下一轮若是实质内容,updater 照常运行
   (B7 实测:4 条"生而冻结"的首轮线 4/4 后来都拿到了真摘要)。
2. **step_4 用 `getattr(ctx, "no_durable_topic", False)` 读** —— 老代码树
   没有此字段时行为退化为"不冻结",这是重演装置 --src-root 指旧树时的
   正确语义(装置 v2.1 曾因漏抄这半句契约损失过两卷的测量效度)。
3. 冻结的下游代价已定价(B7FINAL 观测 6:冻结过的簇后续零重叠 +22~26pp)——
   这是 M6 修复三选一的输入,不是本字段的 bug。


## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

RunContext 新增 turn_profile 输入字段（None=普通路径）。

> 2026-05-29：删除 `evermemos_task` 字段（EverMemOS 整体移除）。

# context.py — AgentRuntime 执行流水线的共享状态容器

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

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

## 2026-08-12 — `on_plain_text_delivery`

team 房间把纯文本贴进房间的回调,只有 MessageBusTrigger 的 team 分支会传,其余场景
为 None。挂在 turn 上而不是放在 `run()` 之后,理由见
[[step_3_agent_loop]]:会话行在 run 内部就写完了,事后发生的投递没法被记成回复,
而乐观记账正是要消除的那个谎。

## 2026-08-18 — 移除 team-room 平台投递字段

`on_plain_text_delivery` 一族从 RunContext 上删除。它承载的是「团队房间的纯文本回复由平台
张贴」这个机制，而房间现在收 `message_team` 工具调用，所以字段没有生产写入方了。原注释解释
它为何必须在 `run()` 之内发生（chat 行在 run 内写，之后的张贴无法被记成回复）—— 那条约束
现在由 `post_team_reply` 在工具调用里满足。

## 2026-08-21 — RunContext.steering

新增 `steering: Optional[Any]`(run 的 SteerChannel;None=不可 steer)。和 `cancellation` 并列,是
run-start→loop 的活控制对象;由 `run()` 填、step_3 读并传给 driver。

## 2026-09-04 · ingress triggers (batch 3c.3)

`registries` — the runtime's plugin registries (None → process `KERNEL_REGISTRIES`), so steps can fire host hooks against the same registries the pipeline/profile resolution used (scenario tests use private registries).
