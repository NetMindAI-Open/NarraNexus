---
code_file: src/xyz_agent_context/agent_runtime/run_collector.py
last_verified: 2026-07-30
stub: false
---

# run_collector.py — 统一的 AgentRuntime 消息收集器

## 2026-07-30 (二次) — 独白并入改为显式 opt-in（`include_monologue`）

PR #203 review 裁定（fix-first #1）：独白并入 output_text 是全局生效的，但
只有 team room 的 prompt 对 agent 说过「你的明文会被发出去」。peer DM→
收件箱、A2A 这些面上，NexusPower 宪法承诺明文私密——全局并入等于把 agent
以为没人看的内部斟酌公开。**显式选择「收窄」方案**（review 给的二选一）：
`collect_run(include_monologue=False)` 默认关闭，只有 prompt 已兑现「明文
即送达」契约的调用方（今天 = bus 团队房间，trigger 传 `is_team`）打开。
另修 on_progress 相位：带独白的 thinking 帧上报 "response"（agent 在说话）
——但只在 opt-in 的 surface 上（独白私密时没人收到那段文本，上报「回复中」
是假象）；activity 视图不再在产出房间回复时显示 thinking。承重接线
`include_monologue=is_team` 由 tests/message_bus/test_team_monologue_wiring.py
锁定（team owner → True / peer → False），防止重构漏 kwarg 让房间静默哑掉
（#203 事故形态）。

## 2026-07-30 — output_text 纳入 NexusPower 独白（群聊 @mention 无回复修复）

`output_text` 的语义从「AGENT_RESPONSE delta 拼接」升级为「agent 说的话」：
AGENT_RESPONSE delta + `AGENT_THINKING.monologue` 子集按到达顺序拼接。
NexusPower 独白契约下 assistant 明文以 thinking 形态流出（monologue 标记），
它就是 claude driver 会走 AGENT_RESPONSE 的那段文本；team room 契约「明文
自动上墙」依赖 output_text，此前拿到空串导致 @mention 回复整段蒸发（dev
evt_238abc4b0b0c4dca：final_output 里躺着完整回复、房间零消息）。provider
CoT 的 monologue 恒为空串，不会把推理泄进 IM 转发。

## 2026-07-30 — 捕获 Step-0 的 event_id

`RunCollection.event_id` + 可选 `on_event_id` 回调：Step 0 完成的
ProgressMessage 已在 `details.event_id` 里携带本 turn 的 events 行 id，
collect_run 在消费循环里抓第一个出现的 id（first-id-wins，最多回调一次），
让 bus 侧（`TurnActivity.note_event_id`）在 turn 还在跑时就能把活动行绑到
events 行——这是团队房间 UI 拉取"刚跑完那轮完整 event_log"的缺失一环，
且完全不触碰 runtime 本身。回调异常吞掉（状态上报绝不能弄坏 run，与
on_progress 同一纪律）。

## 2026-07-27 — 事件类型字面量收敛到 loop/events.py 常量

六种事件形状的字符串字面量改为 import `loop/events.py` 的常量
（TYPE_RAW_RESPONSE_EVENT 等），值逐字节不变——纯机械替换，行为零变化。
事件契约自此有唯一事实源，详见 events.py.md。


## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

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

## 2026-07-22 — opt-in on_progress callback

`collect_run` gained an optional `on_progress(kind, tool_name)` awaited once per observed
message (kind ∈ thinking|tool|response|error). Opt-in (default None → zero overhead for every
existing trigger); the team bus path uses it to mirror a live activity status
([[_bus_activity]]). Never raises — exceptions are swallowed so status can't break the run.
