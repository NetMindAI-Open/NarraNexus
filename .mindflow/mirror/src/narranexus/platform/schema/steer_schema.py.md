---
code_file: src/narranexus/platform/schema/steer_schema.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-24 — docstring 订正:owner_chat 首落地**不走**本 inbox

模块 docstring 原来把 owner-chat「跑 chat memory path」列为本 inbox 存在的理由之一。**订正**:owner_chat 的首个落地(chat WebSocket,PR #355,见 [[websocket.py]])**不经这个 inbox**——它把插话直接 push 进在飞 run 的进程内 `SteerChannel`(ephemeral,上限在 WS 写边用本 repo 同两个常量 `MAX_CONTENT_BYTES`/`MAX_UNCONSUMED_PER_RUN` 重加)。所以该路径的插话**效果**持久(它折进本轮、塑形 assistant 回复,chat memory 会存回复),但**字面 owner 消息**尚未写进 chat memory——消费时补写(刷新历史保真+下轮 recall)是**scoped follow-up**。别把本 inbox 当 owner_chat 的 path-of-record,直到那个 follow-up 落地。

# steer_schema.py — 运行中插话的一条注入

`SteerInjection`:路由进"已经在跑的 turn"的一条消息(而非触发新 turn)。**这张表的正当理由是
解耦,不是持久化**——team 消息本来就在 bus_messages,单为持久化再存一份是冗余;但**不仅仅是
bus**:单聊插话走 chat 记忆路径、根本不进 bus_messages,所以"查 bus_messages"会漏掉一半
producer。统一 inbox 让 feeder 只 drain 一处(同 instance_artifact_events 的 outbox 模式)。
`consumed_at` 是 bus 给不了的**per-run 游标**(bus 的 (agent,channel) 游标是 trigger 的,
且一个 agent 可能多 run 并发)。

- `source` 是**闭集 `Literal["team","owner_chat"]`**(模块级别名,同 schema/ 的 ArtifactKind/EmbedMode),
  不是自由 str——prompt 层按它 branch 措辞,打错字必须在边界炸,不能落 fallback。加新 producer 在这里扩集。
- **三个 store-assigned 字段**(producer 传了也被覆盖):`id`(到达序+游标单位)、`created_at`(DB 默认盖章,
  不是"源发送时间";要那个另开列)、`consumed_at`(drain 时盖)。
- `consumed_at` 只在**单 drainer** 下给 at-most-once,**不是锁**;并发 drainer 不受它保护(见
  [[steer_inbox_repository.py]] 投递语义)。
- 收紧成 `Literal` 后,`SteerInjection(**row)` 读路径遇脏行会**整批** ValidationError——现在表空,收紧零成本;
  有数据后再收就得先设计脏行降级。

- `run_id` **不透明**:本 schema 不知道 orchestrator 怎么标识一个 live run(那是 RunRegistry 的事),
  只要 producer 与 drainer 认同一个句柄——存储层与路由设计解耦。
- `source`(team / owner_chat)记哪个 producer 写的,供 prompt 层措辞(队友房间消息 vs 主人插话,
  机制同、措辞不同)。IM 触发 v1 不做。
- `id` 是到达序 + 消费游标单位,store 赋值故落库前为 None;`consumed_at` None=待消费,盖章=已被 run
  drain,防二次注入。注入 append-only,不改前行内容。

Batch 6b.2b: defines `STEER_ID_KEY = "_steer_id"` (previously in the NexusPower core) so the platform steer channel does not import the framework plugin.
