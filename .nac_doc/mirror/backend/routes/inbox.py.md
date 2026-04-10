---
code_file: backend/routes/inbox.py
last_verified: 2026-04-10
stub: false
---

# routes/inbox.py — Agent 收件箱路由

## 为什么存在

`MessageBus` 系统让多个 Agent 之间可以互相发消息（通过 `bus_channels` 和 `bus_messages` 表）。这个路由把 Agent 的所有消息频道和消息暴露给前端，以 "rooms + messages" 的格式输出，兼容前端原有的 MatrixRoom UI 组件（从 Matrix 协议迁移过来的界面）。它还提供标记已读的接口。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(inbox_router, prefix="/api/agent-inbox")`；前端收件箱面板
- **依赖谁**：
  - `xyz_agent_context.utils.db_factory.get_db_client` — 直接查询 `bus_channel_members`、`bus_channels`、`bus_messages`、`agents` 表

## 设计决策

**已读状态用游标而非逐条记录**

已读状态用 `bus_channel_members.last_read_at`（或 `last_processed_at`）游标来跟踪，而不是在每条消息上存 `is_read` 标志。比较 `message.created_at > cursor` 来判断是否未读。好处是不需要为每条消息维护读取状态表，代价是游标只能单调推进——如果消息乱序（实践中不会，消息按时间写入），可能判断不准确。

标记已读时只推进游标到该消息的时间戳（`MAX` 语义：只在游标比消息时间更早时才更新），所以标记早期消息不会影响更新消息的已读状态。

**前端 MatrixRoom 兼容格式**

输出结构是 `{rooms: [{room_id, room_name, members, unread_count, messages, latest_at}]}`，这个格式是为了复用前端原来基于 Matrix 协议设计的 UI 组件。`members[].matrix_user_id` 是为了兼容性保留的字段，值等于 `agent_id`。

**所有逻辑在一个文件**

没有对应的 Repository 或 Service，所有数据库查询直接在路由函数里。原因是 MessageBus 功能相对独立，代码量不大，引入单独的 Repository 层会增加文件数量但收益有限。如果 inbox 逻辑变复杂，应该考虑下沉到 `xyz_agent_context` 核心包。

## Gotcha / 边界情况

- **游标字段优先级**：代码里用 `r.get("last_processed_at") or r.get("last_read_at") or "1970-01-01"` 取游标，优先用 `last_processed_at`（ModulePoller 更新的），fallback 到 `last_read_at`（前端显式标记的），再 fallback 到纪元时间（全部视为未读）。这个优先级反映了两种"已处理"的语义：一种是 Agent 自己处理完，一种是用户看过了。
- **消息倒序后重新正序**：`bus_messages` 查询用 `ORDER BY created_at DESC LIMIT N`（取最新 N 条），然后 `reversed` 恢复正序。这是为了同时满足"只取最新的 N 条"和"按时间正序展示"两个需求。
- **`limit=-1` 表示无限**：`effective_limit` 的逻辑：`limit < 0` 用 9999，`limit = None` 用 50，`limit >= 0` 用 limit 值。这个约定只在代码里，接口文档里写的是"Max messages per channel (-1 for unlimited)"。

## 新人易踩的坑

SQL 查询里用了 `%s` 占位符（MySQL 风格），靠 `db.execute` 内部的 SQLite 适配层自动转换为 `?`。如果直接用 `db._backend` 执行原始 SQL，需要自己处理方言差异。
