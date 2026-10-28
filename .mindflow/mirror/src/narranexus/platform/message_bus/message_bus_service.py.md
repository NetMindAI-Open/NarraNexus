---
code_file: src/narranexus/platform/message_bus/message_bus_service.py
last_verified: 2026-09-09
stub: false
---
## 2026-09-09（review M1）— 协议加 `get_message(message_id)`

单条按 id 读回。发送工具记回执要知道 bus 给这对 agent 找到/新开的 DM 是哪个 channel，
刚写的行是唯一知道的地方；此前工具直接 `db.get_one("bus_messages")`，现在走协议方法。
`LocalMessageBus` 实现，`CloudMessageBus` 仍 `NotImplementedError`。

## 2026-09-09 — `send_message` / `send_to_agent` 增加 keyword-only `part_index/part_count`

长消息分片契约（[[multipart]]）。放在 `*` 之后：既有位置调用方不可能被重绑，也不必再
往「附加在最后」的注释上加名字。`_post_to_room` 漏斗**不**承接（房间行是平台自己的一句话，
永远不是片段），`test_team_message_segments` 的漂移测试把两者列入豁免并写明理由。

## 2026-08-14 — `segments` 参数 + `get_messages_before`

`send_message` 增加 `segments`（独白/回复的分界，见 [[schemas.py]]），**追加在最后**并
且必须留在最后：这个方法有位置传参的调用方，插在中间会静默重绑每一个。这条规则由
`test_team_message_segments` 断言。同一次合并里 dev 也追加了 `routed_by`，两个都在，
`segments` 仍在末位。

`get_messages_before` 进抽象契约（实现见 [[local_bus.py]]，cloud 侧是 stub）。它和
`get_messages(since=…)` **刻意不对称**：`since` 返回游标之后**最旧的** n 条（补进度不能
跳过任何一条），`before` 返回游标之前**最新的** n 条（往上翻要的是屏幕正上方那一页）。
写反了不会报错，只会产出中间静静少一段的 transcript。

## 2026-08-07 — send_message / send_to_agent 增加 root_run_id

抽象契约跟着 local 实现走,否则协议层无法透传。语义见 [[local_bus]]。

## 2026-08-03 — sender_turn_source in both send contracts

`send_message` / `send_to_agent` 抽象签名补上
`sender_turn_source: str | None`。前一轮只改了 [[local_bus]] 实现和调用方,
协议层漏了(PR #229 review 抓到):对协议做类型检查看不到这个参数,未来的
cloud 实现会在每个调用方都在传的 keyword 上 TypeError。签名漂移不会在
import 时报错,靠 `tests/message_bus/test_bus_service_protocol.py` 守住
(inspect.signature 逐参数比对三层)。语义见 [[schemas]] 与 [[hook_schema]]
的 `BUS_ERRAND_TURN_SOURCE`。

## 2026-07-31 — event_id in the send contract

`send_message` abstract method gained `event_id: str | None` (the turn that
produced the message; see [[schemas]]). Same one-batch pattern as the earlier
`attachments` addition: [[local_bus]] persists it, the [[cloud_bus]] stub
carries the param and still raises.

## 2026-07-22 — get_recent_messages joins the ABC

`get_recent_messages(channel_id, limit)` (newest N, reordered ASC — the
recent-scrollback complement to `get_messages`' oldest-N) was added to
[[local_bus]] in PR #141 but only on the implementation class; it's now an
abstractmethod here with a `NotImplementedError` stub in [[cloud_bus]], same
as the `attachments` params were added across all three in one batch. The
trigger currently type-hints `LocalMessageBus` so nothing breaks at runtime
either way — this keeps the interface surface honest.

## 2026-07-20 — attachments in the send contract

`send_message` / `send_to_agent` abstract methods gained `attachments: list[dict] |
None`. Implementers persist it; `CloudMessageBus` stub carries the param but still
raises NotImplementedError. Files are references, not bytes — see
[[_bus_attachment_impl]].

# message_bus_service.py — MessageBus 统一抽象接口

## 为什么存在

当前实现是 SQLite/MySQL 的本地版本，未来可能迁移到云端消息队列（Redis Pub/Sub、Kafka 等）。`MessageBusService` 抽象类是隔离层，让所有消费方（`MessageBusTrigger`、`MessageBusModule` 的 MCP 工具）面向接口编程，切换实现时不需要修改消费方代码。

这也是系统不强依赖某一个框架原则的具体体现——抽象层允许将来替换底层实现而不破坏上层逻辑。

## 上下游关系

**被继承**：`LocalMessageBus`（SQLite/MySQL 实现）和 `CloudMessageBus`（占位 stub）继承它。

**被消费**：`MessageBusTrigger` 持有一个 `LocalMessageBus` 实例（类型标注是 `LocalMessageBus` 而非 `MessageBusService`，是历史遗留——可以改成 `MessageBusService` 以更严格遵守 LSP）；`module/message_bus_module/_message_bus_mcp_tools.py` 里的 MCP 工具函数接受 `MessageBusService` 参数。

**依赖谁**：`schemas.py` 里的四个数据模型（`BusMessage`、`BusChannel`、`BusChannelMember`、`BusAgentInfo`）。

## 设计决策

投递模型是 **cursor-based**：`BusChannelMember.last_processed_at` 记录每个 Agent 在每个频道里处理到哪条消息，`get_pending_messages()` 返回 `created_at > last_processed_at` 的消息，`ack_processed()` 推进这个时间戳游标。这比"已读/未读"标记更健壮，不需要对每条消息记录处理状态，只需要一个时间戳。

**Poison message 过滤**：连续投递失败 3 次（`failure_count >= 3`）的消息被跳过，防止一条损坏消息阻塞整个队列。失败记录通过 `record_failure()` 累积，`get_pending_messages()` 的实现里需要过滤掉这类消息。

消息有 `mentions: List[str]` 字段，值是 agent_id 列表或 `["@everyone"]`。`MessageBusTrigger` 用这个字段决定是否激活特定 Agent。

## Gotcha / 边界情况

`send_to_agent()` 是便捷方法，内部会自动创建两个 Agent 之间的私信频道（如果不存在）再发送。`send_message()` 需要提前知道 channel_id，更底层。两者都是合法的发消息方式，但语义不同。

`get_unread()` 和 `get_pending_messages()` 的区别：前者基于"已读游标"（`last_read_at`），后者基于"已处理游标"（`last_processed_at`）。在 MessageBus 里，"读取"（Agent 看到消息）和"处理"（AgentRuntime 处理完成）是两个独立的时间戳，以支持"Agent 看到消息后正在思考"的状态。

## 新人易踩的坑

`MessageBusService` 是纯 ABC，不含任何实现。直接实例化会报错。所有使用时应该实例化 `LocalMessageBus(backend=...)` 或通过 `_get_bus()` 工厂函数获取。

## 2026-08-11 — 协议新增 `ack_read` / `count_unread`,`get_unread` 加 `limit`

与 `local_bus` 的三个新契约对齐。协议里写清了 `limit=None` 不是"默认值"而是**一种
必要模式**:决定"这一轮回复覆盖了哪些消息"的调用方必须拿到全量,给窗口会让更老的
已回复消息永远留在未读里。

## 2026-08-12 — `send_message` 增加 `routed_by`

协议同步。`test_bus_service_protocol.py` 在实现漏跟时会红 —— 这轮它就抓到了
`CloudMessageBus` 漏改。

## 2026-08-12 — 协议新增 `has_unread_before`

存在性判断,实现方不得为此把积压物化。语义见 [[local_bus]]。

## 2026-08-14 — 新增 `has_message_from_turn`

一个存在性问题:某 agent 在某轮里有没有往某频道发过东西。键是 turn id,因为平台代发与
agent 自己调工具发**都**盖这个 id,一个方法覆盖两条投递路径。实现方不得为此把消息全量
拉回内存 —— 调用点在一轮已经降级的 turn 收尾处,那里最不该再加一次全表搬运。

## 2026-08-14 (补) — `send_to_agent` 签名加 `event_id`,并订正 `send_message` 的说法

与 `send_message` 对齐:两条 agent 主动发消息的路径都必须能记下「这是哪一轮」。
None 表示说不准,不是猜一个。

同批订正 `send_message` 那个形参的 docstring —— 它原来写「agent replies posted by
the trigger」,而 `send_to_agent` 新写的是正确版本,同一个 protocol 里同一个参数两个
兄弟方法两种说法。这不是措辞洁癖:**新 bus 实现的作者第一眼看的就是这份 protocol**,
照旧说法实现的云端 `send_message` 会不透传 agent 自发消息的 `event_id`,于是
`has_message_from_turn` 对那一半永远返 False,房间里出现「投递失败 ⚠️」而它上方就摆着
agent 刚说的那句话 —— 正是本 PR 存在的理由,换一个实现复现一遍。


## 2026-08-18 — 协议里声明 `send_to_agent` 的 `ValueError`

`from_agent == to_agent` 现在抛 `ValueError`，而这条契约必须写在**协议**上而不是只写在
[[local_bus.py]] 里：`CloudMessageBus.send_to_agent` 会照着协议实现，跳过这道守卫就等于**重现**
缺陷而不是继承修复。

缺陷本身：私聊频道的查找把频道对着两条成员行 join，同一个 id 传两次会被**同一条成员行**满足，
于是该 agent 所属的任意 direct 频道都匹配 —— 发送落进任意一个 peer 的会话并把对方唤醒。
`direct_channel_sql` 的 docstring 里也写了同一条不变量，因为它已经有两个调用方，第三个否则也会踩。

## 2026-08-23(补)— get_pending_messages 加 channel_id 抽象签名

抽象契约 `get_pending_messages(agent_id, limit=50, channel_id=None)`:channel_id 非空时 scope 到单房间(per-lane 调用方
需要,LIMIT 落单房间),None 保持跨 channel 旧行为。实现见 [[local_bus.py]];`cloud_bus` 是 NotImplemented stub 但签名同步。
