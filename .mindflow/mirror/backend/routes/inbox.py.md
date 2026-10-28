---
code_file: backend/routes/inbox.py
last_verified: 2026-08-21
stub: false
---

## 2026-08-21 — 消息卡 outbound `sender_name` 用解析出的显示名

08-19 把 `members[0].agent_name` 修成解析出的 `agents.agent_name`,但每条消息卡的
`sender_name` 仍是「写入时存好」。问题在:agent 自己的 **OUTBOUND** 行由 `inbox_recorder`
写入时 `sender_name` 存的是**空串**(记录时它没有显示名),于是这里的 `or` 兜底掉到
`sender_id`,卡片显示裸 `agent_<hex>` id;对方(INBOUND)行落库时带了真实名字,故一直正确。
现在 outbound 复用整请求已解析一次的 `agent_display`(和 `members[0]` 同一个值),inbound
仍走落库的 `sender_name`。响应**结构**不变;因为是读时解析,历史空名行也一并自愈,且随改名即时反映。

## 2026-08-19 — members[0] 显示名 + mark_room_read 裸 naive UTC

两处本轮改动:(1) `get_agent_inbox` 的 `members[0].agent_name` 从裸 `agent_<hex>` 改成解析出的 `agents.agent_name`(整请求一次 `get_one`,非 N+1;对方那栏用落库的 `counterpart_name` 本就正确)。(2) `mark_room_read` 的 `last_read_at` 写裸 naive UTC(去掉 `+00:00` 偏移)——MySQL `DATETIME(6)` 下带偏移字面量会被 session tz 转换,与 `mark_message_read` 的裸游标不一致会在非 UTC 库上静默错标已读;SQLite 侧读时统一归 UTC-aware,本改动只对 MySQL 生效。响应**结构**不变。

## 2026-08-17 — 面板改读 inbox 自己的表，且只列「你不在场的对话」

此前它按 `bus_channel_members` 列出 agent 所在的**全部**频道，于是一个面板里混着三种
互不相干的东西：IM 会话、agent 之间的私聊、以及**已经有专属 UI 的 team 房间**。

更要紧的是**游标纠缠**：面板的「标记已读」写的是 `bus_channel_members.last_read_at`，
而那正是决定 agent 下一轮上下文的闸门——**用户在界面上点一下「已读」，改变的是 agent
下一轮看到的东西**。这个纠缠出过事：早先用 `last_processed_at` 算未读数，trigger 每轮都
推进它，计数恒为 0；前端只在计数 > 0 时才发标记已读请求，于是唯一能推进 `last_read_at`
的控件**永远点不到**，恰好就在积压在增长的那些房间里。

现在：`inbox_threads.last_read_at` 是**用户的**阅读状态，碰不到 agent 的任何东西。

**team 房间和 owner 主聊天不在这里**（决策 ②）。规则一句话：*inbox 是你不在场的那些对话。*
它们各自的未读信号本来就有（`TeamWithMembers.last_message_at/preview/author`；主聊天窗自身），
放进来只会得到**两份互相打脸的转录**。

响应结构（rooms / messages / unread_count / latest_at）刻意保持不变，前端不用改。
`sender_name` 现在**写入时就存好**，不再每次请求去 `bus_agent_registry` 反查伪 agent
——那些伪 agent 行正是本次要移除的东西之一。

## 2026-08-12 — inbox 路由补 ownership（SEC-03 读 + SEC-05 标已读，Mark IDOR 批）

`GET ""`（get_agent_inbox）、`PUT /{message_id}/read`、`POST /rooms/{room_id}/read` 原来直接吃 query 的 `agent_id`、无身份校验——任何登录用户可读他人 agent 整个收件箱、或篡改其已读游标（`mark_room_read` 原只验频道成员≠owner）。修复：三个 handler 加 `request` 参数，在 `try` 之前调 `assert_owned(request, agent_id)`（放 try 外，让 403/404/503 直接透出，不被 `except` 压成 dict）。

## 2026-08-11 — 内部错文案脱敏（安全审计 P2-2）

各 `except` 的 `error=str(e)` 收敛为固定文案（如 “Failed to mark room read.”），原始异常只进日志。

## 2026-07-20 — bus attachments surface in the inbox

Each inbox message now carries `attachments` (parsed from the `bus_messages.attachments`
JSON column). New `GET /attachments/raw?path=<rel_path>` streams a shared-area bus file
after gating it to the authenticated user via
`message_bus.attachments.resolve_shared_file_for_user` (facade over [[_bus_attachment_impl]]) (path's first segment must equal the
user_id; no traversal outside `{base}/{user_id}`). This is the DM/peer counterpart to the
team-chat attachment surface in [[teams]]; both serve through this one endpoint.

## 2026-07-03 — message_id tie-break in the per-channel message query

``ORDER BY created_at DESC`` gained ``, message_id DESC``. The writer stamps
a turn's inbound/reply one microsecond apart so created_at already orders a
turn; message_id is a determinism backstop for two turns completing in the
same microsecond, so the order never flickers between requests. created_at
reaches the client as a microsecond ISO string via _to_iso (already
lexicographically sortable) — the frontend relies on that precision.

## 2026-05-28 — POST /rooms/{room_id}/read (room-level mark-all-read)

New endpoint for the "click the channel row → clear that channel's
unread" UX in [[AgentInboxPanel.tsx]] / [[InboxPanel.tsx]].

Why not reuse `PUT /{message_id}/read`: `get_agent_inbox` caps each
channel's `messages[]` array at 50 entries, but `unread_count` is
computed against ALL `bus_messages` rows. A channel with 100 unread
+ only 50 loaded → marking the latest VISIBLE message left a 50-item
unread tail behind, the badge never zeroed. The room-level endpoint
advances `bus_channel_members.last_read_at` to NOW (server UTC ISO
8601), guaranteeing zero residual unread regardless of how many
messages were in the response.

Guards:
- Caller must be a member of the channel (`bus_channel_members` row
  exists) — otherwise returns `{success: False, error: ...}` rather
  than silently passing a zero-row UPDATE.
- UPDATE keeps the same `last_read_at IS NULL OR last_read_at < %s`
  monotonic guard the per-message endpoint uses, so clock skew or a
  parallel update that already advanced past us is not rolled back.
- Idempotent: re-clicking a fully-read channel is a no-op at the DB
  level (the guard NOPs).

Test pin: `tests/backend/test_inbox_mark_room_read.py` (6 cases).

# routes/inbox.py — Agent 收件箱路由

## 2026-05-19 — `_to_iso` 时间归一化

`bus_messages.created_at` / `bus_channel_members.last_*_at` 都是
DATETIME(6) 列。在 MySQL 后端 aiomysql 把它们反序列化成 `datetime.datetime`；
在 SQLite 后端是字符串；而 cursor 的默认 fallback 是字面量
`"1970-01-01"`。三种类型在 `>` / `<=` 比较时混型会抛
`TypeError: '>' not supported between instances of 'datetime.datetime'
and 'str'`（生产环境 2026-05-19 backend 22 条 ERROR）。

新加 `_to_iso(value) -> str` helper（datetime → isoformat，None → ""，
str → as-is），在 cursor 构造 + 每个比较/排序点都 wrap 一层。
ISO 8601 字符串按字典序就是时间序，跨后端等价。


## 为什么存在

`MessageBus` 系统让多个 Agent 之间可以互相发消息（通过 `bus_channels` 和 `bus_messages` 表）。这个路由把 Agent 的所有消息频道和消息暴露给前端，以 "rooms + messages" 的格式输出，兼容前端原有的 MatrixRoom UI 组件（从 Matrix 协议迁移过来的界面）。它还提供标记已读的接口。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(inbox_router, prefix="/api/agent-inbox")`；前端收件箱面板
- **依赖谁**：
  - `narranexus.platform.utils.db.db_factory.get_db_client` — 直接查询 `bus_channel_members`、`bus_channels`、`bus_messages`、`agents` 表

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

## 2026-08-11 — 未读数只认 `last_read_at`

原先取 `last_processed_at or last_read_at`。两根游标回答的是不同问题:
`last_processed_at` 是 trigger 的书签(「我把这个 agent 推过这里了」),`last_read_at`
才是 agent 真正被展示过的位置 —— 也是本面板「标记已读」按钮写的那一根。

优先取前者让两者往最坏的方向背离:trigger 每轮轮询都推进它,于是计数恒为 0;而前端
只在计数大于 0 时才发那次标记已读请求;于是**唯一能复位 `last_read_at` 的控件不可达**,
恰恰是在积压正在增长的那些房间里。面板说"这儿没东西",而每一轮的上下文都在被塞进
它否认存在的那一堆。

修完之后 inbox 会开始显示真实积压(可能不小)。这是特性不是回归 —— 用户终于看得见,
也就点得动了。
