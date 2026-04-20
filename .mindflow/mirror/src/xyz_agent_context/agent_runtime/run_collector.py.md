---
code_file: src/xyz_agent_context/agent_runtime/run_collector.py
last_verified: 2026-04-20
stub: false
---

# run_collector.py — 统一的 AgentRuntime 消息收集器

## 为什么存在

`AgentRuntime.run()` 是一个 async generator，流出 5 种 MessageType
（AGENT_RESPONSE / AGENT_THINKING / TOOL_CALL / PROGRESS / ERROR）。每
个非-WebSocket 消费者（LarkTrigger / JobTrigger / MessageBusTrigger /
ChatTrigger A2A）都要把这些消息汇成一个可返回的结果——文本、工具调用、错误。

在这个文件引入之前，4 个消费者各自复制了同一段 `async for` 循环，且**都
只处理 AGENT_RESPONSE，静默丢弃 ERROR**。这是 Bug 2（Lark 石沉大海）
的直接原因：runtime 明明在传送带上放了 ERROR 消息，Lark 却只听
AGENT_RESPONSE，导致用户看不到任何反馈。

本文件提供一个 `collect_run()` 函数，把"收"集中一处，"展示策略"由每个
trigger 自行决定。新增 trigger（Telegram/Slack/Discord 等）直接调
`collect_run()`，不会再漏 ERROR。

## 上下游关系

**上游 / 消费者**（使用本模块）：
- `module/lark_module/lark_trigger.py` — `_build_and_run_agent`
- `module/job_module/job_trigger.py` — Job 执行主循环
- `message_bus/message_bus_trigger.py` — `_invoke_agent_runtime`
- `module/chat_module/chat_trigger.py` — A2A tasks/send handler

**下游**（本模块调用）：
- `AgentRuntime.run(**kwargs)` — 通过 rebuildable kwargs 转发所有参数
- `schema.runtime_message.MessageType` — 用于 message_type 比对

**不用本模块的地方**：`backend/routes/websocket.py`。WebSocket 不"收"
消息，而是把每条消息流式转发给前端；前端 chatStore.ts 已经正确处理
ERROR → currentErrors。

## 设计决策

**`RunCollection` 是 data-only**。它不做展示决策（展示策略是每个
trigger 的独特逻辑——Lark 发 IM 友好文案，Job 标 failed 状态，
MessageBus 返回结构化错误对象给 sender agent，A2A 写 TaskState.FAILED
消息）。把"收"和"用"分开意味着未来新增 MessageType 只改本文件一次。

**`last error wins`**。多个 ERROR 消息（少见但理论可能）时保留最后一条
——最具体的失败信息。typical case 只有一条 ERROR，行为等价。

**`raw_items` 保留所有原始负载**。LarkTrigger 需要从 TOOL_CALL 事件
的 raw 载荷里抽出 agent 实际发出的 `lark_cli im +messages-send` 文本
（`_extract_lark_reply`）。把 raw 收集成 list 让 Lark 能自己查，其他
trigger 可以忽略。

**`**extra_kwargs` 透传**。`trigger_extra_data`、`job_instance_id`、
`forced_narrative_id`、`pass_mcp_urls`、`cancellation` 等 trigger 特
定参数原样传给 `runtime.run`。collect_run 自身不关心这些参数的语义。

## Gotcha / 边界情况

- **AGENT_RESPONSE 的空 `delta`** 被丢弃（不拼接）。SDK 有时会 emit
  空 delta 作为 keepalive；把空串拼进文本是无用噪音。
- **消息没有 `message_type` 属性** 时整条消息被忽略（不是假设 delta
  字段存在）。这防止 SDK 变更后出现静默破坏。
- **异常不在 `collect_run` 内被捕获**。`AgentRuntime.run` 内部的
  exceptions 会照常向上抛到 trigger 调用处，trigger 可以包 try/except
  做自己的失败兜底。collect_run 只管"正常流完"的情况下的归档。

## 新人易踩的坑

- `RunCollection.is_error` 只读，`@property`。检查错误时用
  `if result.is_error:` 而不是 `if result.error:` —— 后者在空
  dataclass 上也会 truthy 判 False，但语义不直观。
- `RunError` 是 `@dataclass(frozen=True)`，不能就地修改。如果某个
  trigger 想附加自己的上下文（例如 Lark 想加 chat_id），自己包一层
  本地 dataclass，不要改 RunError 实例。
- 和 `format_lark_error_reply` 的关系：那是 Lark-specific 的"怎么把
  RunError 渲染给 IM 用户看"函数，住在 lark_trigger.py。本模块只提供
  RunError 数据结构本身。
