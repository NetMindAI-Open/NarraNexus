---
code_file: src/narranexus/platform/message_bus/local_bus.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-09（review M1）— `get_message(message_id)`

`_row_to_message` 的单行读回，供发送工具取刚写行的 channel_id。

## 2026-09-10（PR #389 I1）— 超长改抛 `BusMessageTooLarge`（只带事实）

`ValueError` 子类、携带 `size`；出路文案由各调用方补（见 [[multipart]]）。

## 2026-09-10（review r2 M3）— `canonical_ts` 定义移到 [[schemas]]，本文件 re-export

## 2026-09-10（review r2 C2）— `_resolve_part_group` 校验 `part_count <= MAX_MESSAGE_PARTS`

每一块都查、所以 1/N 就拒（`too_many_parts_reason`），与字节预算并列。

## 2026-09-09（review I7/M6）— 组字节预算在 `_resolve_part_group` 执行；补索引

块 ≥2 时再查一条 `SELECT content ... WHERE channel_id=? AND part_group=?`（MySQL 孪生已钉）
累加字节，超 `MAX_MULTIPART_TOTAL_BYTES` 拒绝；1/n 自身超预算也拒。（计数上限
`MAX_MESSAGE_PARTS` 在 r2 C2 恢复，见上。）`bus_messages` 新增 `idx_bus_msg_sender_time (channel_id, from_agent, created_at)`
服务「发件方最近一块」查询，长寿命 DM 不再全表扫。

## 2026-09-09（review I4/M4）— 单行超长在 `send_message` 写入边拒绝

`MAX_BUS_MESSAGE_BYTES`（定义在 [[multipart]]，60_000，低于 MySQL TEXT 65,535）现在在
`send_message` 入口按 UTF-8 字节判、`ValueError(oversize_reason(size))` 拒绝——`message_agent`
/ `message_team` / 平台自己写的行全部经过这一处；工具层不再各自检查（原只装在 `message_agent`）。
平台通知最长 ~450 字符，远在上限之下。multipart 符号改为模块顶部 import（无循环依赖）。

## 2026-09-09（review I2）— `PENDING_BATCH_LIMIT` / `PENDING_BATCH_LIMIT_WIDE` 成为具名常量

`get_pending_messages` 的默认 limit 从字面 50 改为 `PENDING_BATCH_LIMIT`，另有
`PENDING_BATCH_LIMIT_WIDE=500` 供 trigger 在多段组被批次边缘切断且无法推进时二次读取。
LIMIT 语义不变（整条 lane 最老 N 行）。

## 2026-09-09 — 分片消息：`part_index/part_count` 与 `_resolve_part_group`

`send_message` / `send_to_agent` 末尾加 **keyword-only** 的 `part_index` / `part_count`
（`*` 之后，位置调用方不可能被重绑；`test_team_message_segments` 的签名钉子改成断言
`segments` 是最后一个 positional 参数）。写入前 `_resolve_part_group` 解析组：1/n 用自己的
msg_id 开组；i/n（i>1）查同 sender 同 channel **最近一块**（新 raw SQL，MySQL 孪生
`test_multipart_mysql.py`），必须是 i-1/n，否则 `ValueError` 拒绝——放不进组的碎片不落库。
三个可空列 `part_index/part_count/part_group` 经 `_row_to_message` 回到 `BusMessage`。
重组在收件侧 [[multipart]]。

## 2026-08-17 — `send_message` 顺手叫醒轮询循环（跨进程）

插入成功之后调 [[wake_signal]] 的 `bump()`。**放在这里而不是各调用点**，理由和
`_post_to_room` 当初存在的理由是同一个，但强一档：`send_message` 是全仓**唯一**的
`bus_messages` 插入点，所以「发了帖没叫醒」从「一条要记住的纪律」变成**结构上不可能**
——没有第二个插入点可漏。

这也让 [[message_bus_trigger]] 那条结构性守卫测试（禁止本模块内出现
`self._bus.send_message(`）失去存在意义：它守的是「调用方可能忘记唤醒」，而唤醒现在在
写入里面。

**在 insert 之后，不是之前**：信号的含义是「有新活儿」，插入失败了还 bump 会让轮询醒来
发现什么都没有，信号从此不再有意义（测试 `test_a_send_that_fails_does_not_bump` 钉住）。

`bump()` 自身 best-effort、不抛：一个延迟提示不该让发送失败。

## 2026-08-14 — `get_messages_before`：房间的另一个方向

翻历史用的游标。和 `get_messages(since=…)` **刻意不对称**：

- `since` 返回游标**之后最旧的** n 条 —— 补进度不能跳过任何一条，所以从读者所在的位置
  往前走；
- `before` 返回游标**之前最新的** n 条 —— 往上翻要的是屏幕正上方那一页，不是房间历史的
  开头。

任何一个方向写反，产出的都是**中间静静少了一段**的 transcript，而不是一个报错。

游标**开区间**：调用方传的是它已经持有的最旧那条的时间戳。闭区间会让每一页都重发那一条，
前端的合并再把它去重掉——于是每页都比请求的少一条，而且没有任何看得见的原因。

空列表表示"到顶了"。调用方据此停止提供"加载更多"，而不是从"这页比较短"去猜——短页也可能
只是稀疏窗口。

## 2026-08-07 (二次) — 抑制谓词改问「这棵树里有人被停吗」(PR #252 review Critical #1)

初版是 `LEFT JOIN events ON m.root_run_id = e.event_id` + 判断**那一行**的
`cancel_requested_at` —— 即只看**根**有没有被盖旗标。而端点只给 running 的
行盖旗标(终态行绝不盖,否则会成为下一个 run 的陷阱)。

失效场景恰好是委派的**主流形态**:agent 把活交给同伴后自己这一轮就结束
(「发出去、结束本轮、对方稍后回」)。owner 在 roster 上看到同伴在跑、点
停止时,根那一行早已 `completed` 且永远不会被盖旗标 → 谓词读成「没人被
停」→ 排队消息照常投递 → 正是本功能要消灭的打地鼠。

改为相关子查询 `NOT EXISTS (SELECT 1 FROM events e WHERE e.root_run_id =
m.root_run_id AND e.cancel_requested_at IS NOT NULL)`:问的是**整棵树**有没有
任何一个 run 被请求停止,与哪一行是根、根是什么状态都无关。

初版的测试全部只覆盖「根仍 running」,所以这个洞不会红 ——
`test_a_stopped_tree_whose_root_already_finished_still_suppresses` 补上了这
一支。这条 SQL 走 RAW backend(**无 `%s→?` 翻译**),是本次方言风险最高的
一句,已在真 MySQL 上验证(`tests/message_bus/test_cascade_stop_mysql.py`)。

## 2026-08-07 — 被停止的树,排队消息不再唤起 run

`get_pending_messages` 增加 `LEFT JOIN events ON m.root_run_id = e.event_id`
+ `AND (m.root_run_id IS NULL OR e.cancel_requested_at IS NULL)`。

停掉正在跑的轮次**不够**:它们排队中的后续消息会在下一次轮询启动新 run,
用户按了停之后眼看着新活冒出来(设计文档里的"打地鼠")。

- **写成 SQL 谓词而不是逐行判断**:poison 过滤已经是每行一次查询,再加一次
  就是第二个 N+1。
- **NULL root 永不被压制**:用户消息和所有前置列的老行都是 NULL,若 NULL 被
  当成"同一棵树",一次停止会让整张表哑掉。测试专门钉了这条。

`send_message` / `send_to_agent` / `_row_to_message` 同步接受并透出
`root_run_id`。

## 2026-08-04 — send_message/send_to_agent 记录发送方 turn 的种类

新增可选 `sender_turn_source`,落到 `bus_messages.sender_turn_source`:
owner 面 turn(chat/job/…)= 发送方在跑差事、这条是**提问**;`message_bus`
= 它本来就在答同伴、这条是**回复**。[[message_bus_trigger]] 据此选给收件方
哪条指令 —— 靠 channel 排序推断做不到,因为 DM channel 是**对称查找+复用**
的(见本文件 `send_to_agent` 的 direct-channel 查询),opener 永远固定而
差事是双向跑的。`_row_to_message` 同批带出该字段。

## 2026-07-31 — send_message persists event_id

`send_message` gained `event_id: Optional[str] = None`; persisted on the
`bus_messages` row and surfaced by `_row_to_message`. Only the trigger's team
branch passes it (the turn that produced the reply); every other caller keeps
the default None.

> ⚠️ 末句已于 2026-08-14 失效 —— agent 自己的 bus 发送也盖。见本文件 08-14 节。

## 2026-07-28 — batched room pending summary + the poison threshold moves here

`get_room_pending_summary(channel_id, agent_ids)` answers "what is still waiting
for each of you in THIS room" in three queries, independent of member count.
[[teams]]'s activity block previously called `get_pending_messages` per member,
which is one query per member PLUS one `get_failure_count` per pending row — a
6-member room polled every 3s ran ~30 queries a tick, forever, just to render a
status chip.

It deliberately re-implements the same notion of pending (past the member's
cursor, not self-sent, poisoned rows excluded) restricted to one channel and to
@-addressed messages. The cursor comparison happens in Python via `_as_utc`
because the backends disagree on the wire type: MySQL returns naive
`datetime`, SQLite returns the ISO strings `_now_iso` wrote.

`POISON_FAILURE_THRESHOLD` now lives here — `get_pending_messages` is the filter
that enforces it, and it used to be a bare `3` here with a same-valued constant
in [[message_bus_trigger]] whose comment asked a human to keep them in sync.


## 2026-07-22 — get_recent_messages (newest N, chat order)

Added `get_recent_messages(channel_id, limit=20)` — `ORDER BY created_at DESC LIMIT n`
reversed to oldest→newest. `get_messages` is ASC-limited (the OLDEST n), wrong for "recent
scrollback"; this powers the team-room prompt's history window (see [[message_bus_trigger]]).

## 2026-07-20 — multimodal: messages can carry file attachments

`send_message` / `send_to_agent` gained an `attachments: list[dict] | None` param;
`_row_to_message` deserializes the new `bus_messages.attachments` JSON column. When
files are present the `msg_type` auto-flips `text`→`multimodal` (UI/search hint).
Attachments travel by REFERENCE (metadata + base-relative shared-area path), never
bytes — staging + marker synthesis live in [[_bus_attachment_impl]]. No change to
the same-user boundary: files ride the same DM/channel path, so cross-user still
raises `PermissionError`. Insert still goes through the dialect-safe `db.insert`.

## 2026-07-02 — poison threshold now has a detection + recovery path (no code change here)

This file's own behavior is unchanged, but the `failure_count < 3` filter in
`get_pending_messages()` (see Design decisions below) now has two consumers
that didn't exist before: `MessageBusTrigger._notify_permanent_failure`
(`message_bus_trigger.py`) writes an inbox notice once a message's
`bus_message_failures.retry_count` reaches 3, and
`backend/routes/agents/bus_failures.py` lists/clears those rows so the
message is picked back up on the next poll. Neither talks to
`LocalMessageBus` directly for the recovery path — the retry route deletes
the `bus_message_failures` row via a fresh `AsyncDatabaseClient`, not this
class — see that file's mirror md for why.

## 2026-06-24 — raw SQL must use BARE identifiers, not double quotes (MySQL gotcha)

`get_messages` and `get_channel_members` had used double-quoted identifiers
(`SELECT * FROM "bus_messages" WHERE "channel_id" = ?`). SQLite accepts `"..."`
as an identifier quote, but MySQL (prod/dev, no `ANSI_QUOTES`) treats it as a
**string literal** → `ProgrammingError 1064` syntax error. These queries were
latent since the pluggable-DB-backend commit and only surfaced when team group
chat became the first cloud-mode caller of these methods (silent UX: the
team-chat POST 500'd and the composer restored the draft). Fixed to bare
identifiers (`FROM bus_messages WHERE channel_id = ?`), matching every other raw
query in this file. **Rule: any `db.execute` raw SQL here must be dialect-safe —
bare identifiers + `self._db.placeholder`, never `"`-quoted names.** (Same fix
applied to `_team_cascade_depth` in [[message_bus_trigger]].)

## 2026-06-23 — ack_processed canonicalises the cursor timestamp

`ack_processed` now does `up_to_timestamp = up_to_timestamp.isoformat()` when
given a `datetime`, so the stored cursor and `bus_messages.created_at` (both
TEXT, compared lexicographically in `get_pending_messages`) always share the
isoformat `…T…+00:00` shape. Previously a `datetime` could be persisted as
`str()` space-format (`"… …+00:00"`); since 'T' > ' ', every newer message then
looked unprocessed and the agent re-triggered forever. See the matching note in
`message_bus_trigger.py.md` (the call sites also dropped their `str()` wraps).

## 2026-06-08 — bus message search index (projection)

`send_message` now also writes a `memory_bus` index (message content + `source_ref`→bus, tagged `channel:<to>`) so inter-agent messages are findable via `remember`. Append-only — one index per message, no update/dedup (same nature as chat history). Best-effort.

# local_bus.py — MessageBus 的 SQLite/MySQL 实现

## 为什么存在

`MessageBusService` 抽象接口的本地实现。名字叫 "local" 但实际上支持任何 `DatabaseBackend`（SQLiteBackend 和 MySQLBackend 都可以）——"local" 的含义是"非云端 API"，即所有状态存在本地数据库而非远程消息队列服务。

这是生产环境实际运行的实现，`CloudMessageBus` 还只是占位。

## 上下游关系

**被谁实例化**：`message_bus_trigger.py` 里的 `_get_bus()` 工厂函数通过 `LocalMessageBus(backend=db._backend)` 创建实例；`module/message_bus_module/` 的 MCP 工具初始化时也需要一个 `LocalMessageBus` 实例。

**依赖谁**：接受一个 `DatabaseBackend` 实例（不是 `AsyncDatabaseClient`，是更底层的后端接口）；通过 `backend.execute()` 执行原始 SQL；序列化/反序列化 JSON 用标准库 `json`。

## 设计决策

`get_pending_messages()` 的 SQL 逻辑：`created_at > last_processed_at`（或 last_processed_at IS NULL）AND `from_agent != agent_id`（不处理自己发的消息）AND 失败次数 < 3（poison message 过滤）AND 该 Agent 是频道成员（JOIN `bus_channel_members`）。

`send_to_agent()` 的自动创建逻辑：检查两个 Agent 之间是否已有 direct channel（名称用排序后的 agent_id 组合，保证对称唯一），不存在则创建，然后把两个 Agent 都加为成员，最后发消息。这是幂等的——并发发消息可能触发竞态创建两个 channel，但查询逻辑取的是特定名称，后续调用会找到已存在的。

`_generate_id()` 用 `secrets.token_hex(4)` 生成 8 字符的 hex，与系统其他 ID 的生成方式略有不同（其他地方用 `uuid4().hex[:8]`）。功能等价，但格式上 `secrets.token_hex` 是纯十六进制，`uuid4().hex` 也是十六进制——实际是一样的。

## Gotcha / 边界情况

`LocalMessageBus` 接受 `DatabaseBackend` 而不是 `AsyncDatabaseClient`——这个区别很重要。`DatabaseBackend` 是更底层的接口，直接支持 `execute()` 方法。如果你有 `AsyncDatabaseClient` 实例，用 `client._backend` 取底层 backend。

`ack_processed()` 用 UPSERT 逻辑更新 `bus_channel_members.last_processed_at`——如果成员记录不存在（agent 只是消息接收者但不是正式频道成员），这里可能会失败或无效。Agent 必须先通过 `join_channel()` 成为正式成员，`last_processed_at` 游标才能被正常追踪。

## 新人易踩的坑

所有 SQL 里用的是 `%s` 占位符（不是 `?`），这依赖 `DatabaseBackend.execute()` 的参数处理层把 `%s` 自动转成目标数据库的占位符格式。不要改成 `?` 或 f-string 直接拼接。

## 2026-08-11 — 未读的三个契约:`ack_read` / `get_unread(limit)` / `count_unread`

`get_unread` 此前**没有契约**,三个缺陷叠在一起:

1. **不排除自己发的帖。** `get_pending_messages` 从写下来就有 `from_agent != me`,
   这条一直没有。于是 agent 在活跃房间里把自己说的话当成别人的待回消息读回来。
2. **返回最旧 N 条。** `ORDER BY created_at ASC` + 无 SQL LIMIT + 调用方 Python 切片。
   `get_recent_messages` 早就把正确形状(DESC + `reversed()`)连同理由写在 docstring 里
   了,这里没采纳。配上永不推进的读游标,每一轮拿到的是**同一批最古老的消息**,却被
   当作"房间现在的动静"呈现。
3. **无 LIMIT**,整个积压过一趟网络再被切掉。

现在 `limit` 选**最新的 N 条**、仍按阅读顺序返回;`limit=None` 保留全量模式,
**这不是可选项**:模块的 turn 后钩子要拿全量来判断"这一轮的回复覆盖了哪些消息"。
给它一个窗口,一个安静频道就可能被繁忙频道整个挤出窗口 —— agent 回了那个频道,
游标却一动不动,它刚回答过的消息永远留在未读里,下一轮再被要求回答一次。
`test_get_unread_contract.py` 里有一条专门的看门狗钉这个形状。

`count_unread` 是新的:渲染是 `N unread (showing M)`,查询一旦加了 LIMIT,N 就不能
再从结果 `len()` 来 —— 那样 N 恒等于 M,读者永远不知道自己看的是个窗口。

`ack_read` 是 `ack_processed` 在另一根游标上的孪生。两根游标语义不同、不能合并
(`inbox.py` 合并过一次,后果见那份 mirror)。时间戳归一化直接委托给 `ack_processed`
的同一段逻辑而不是重写一遍 —— 那个 `'T'(0x54) > ' '(0x20)` 的坑值不得踩第二次。
它**只前进**:`ack_processed` 能不带这个保护是因为它的调用方总是传批次自己的水位线,
而 `ack_read` 有多个调用点,一个能被往回拨的游标会让已读消息重新冒出来。

## 2026-08-11 (补) — `canonical_ts`:那个坑只留一个落点

两个 ack 各自抄了一遍 `isoformat()` 归一化,而 `ack_read` 的 docstring 却写着它
"delegated" 给了 `ack_processed` —— 注释和代码不一致,偏偏这是个踩过的坑
(`'T'(0x54) > ' '(0x20)`,空格格式的游标沉到所有 `created_at` 之下,消息永远显示
未处理)。抽成模块级 `canonical_ts`,两个 ack 和 trigger 的缺口判定共用一份。

## 2026-08-12 — `send_message` 接受 `routed_by`

纯透传到新列并在 `_row_to_message` 里读回。语义见 [[schemas]]。

## 2026-08-12 — `has_unread_before`:存在性问题用存在性查询回答

`_ack_room_seen` 要判断的是「本轮渲染窗口有没有够到这个 agent 还欠着的底」——
一个布尔。初版做法是无 limit 的 `get_unread` 拉该 agent **所有频道**的全部未读、
为每一行构造 `BusMessage`(含 attachments 的 JSON 解析),再在 Python 里筛。

那正是同一批改动刚从 `get_unread` 里移除的形状(整个积压过一趟网络再被切掉),
而且它在成功路径和被取消路径上各调一次;同一轮里 `after_turn` 还会
再全量捞一次。

现在是 `SELECT 1 … LIMIT 1`,复用 `_unread_where`。副作用同样重要:**排序判据回到
SQL**,不再靠 Python 侧手工复现游标的字典序比较 —— 那等于把一条已经咬过人的规则实现
两遍。

## 2026-08-14 — `has_message_from_turn`:用 turn id 问"这个房间听见它说话了吗"

团队房的失败公告要回答一个此前没人问过的问题:**这一轮有没有任何东西以本 agent 的身份
落进本房间**。问它的场合很窄(平台代发没发生、但这一轮又不算 fatal),答错的代价却是
在房间里贴一条假的投递失败 —— 而房间里同时还摆着 agent 自己发的那句话。

判据用 **turn id**,不是时间窗:两条投递路径(平台在 turn 内代发、agent 自己调
`bus_send_message`)现在都往 `bus_messages.event_id` 上盖同一个 id,所以一次
`SELECT 1 ... LIMIT 1` 就同时覆盖两者。时间窗只能回答"差不多那会儿",而且要再写一遍
本模块刚清理掉的那套时间戳比较 —— 这个仓库为它付过一次学费(见 08-12 那节的
`canonical_ts`)。身份是精确的。

只问存在性:调用方在决定"要不要公告",消息正文与这个决定无关。

## 2026-08-14 (补) — `send_to_agent` 也透传 `event_id`(并作废 07-31 那句)

它本来就是 `send_message` 的一层包装,只是没把新参数往下带。一列的含义如果取决于
**哪个工具写的行**,这一列就没法被查询 —— 归因要么处处都有,要么不如没有。

**上面 07-31 那节的「Only the trigger's team branch passes it … every other
caller keeps the default None」已经失效。** 现在 agent 自己调
`bus_send_message` / `bus_send_to_agent` 发的行也带 id(见
[[_message_bus_mcp_tools]]),包括 **DM 频道的行** —— 而 DM 从来不是「平台代发」。
所以 `event_id IS NOT NULL` **不能**当「这条是平台代发的」过滤条件用;它现在只表示
「发的时候知道自己在哪一轮」。

## 2026-08-12 — `segments` 落库与读回

`send_message` 追加 `segments`（**追加，不插入**——存在位置参数调用方，中间插一个会静默重绑，
本仓库在 `ContextRuntime` 上已经付过这笔学费；有测试钉住它在参数表末位）。

- 空列表存 NULL：它看起来像数据，会诱使读者相信「这轮确实没有分段」；NULL 明确表示「没有记录边界」。
- **坏 JSON 不炸整个房间**：一行手改坏了只丢那条消息的排版（降级），
  而不是让整个 transcript 打不开（故障）。会记 warning，否则就是静默降级。

## 2026-08-18 — 未读谓词排除旧 IM 频道，注入在部署当天就停

2026-08-17 之前 `ChannelInboxWriter` 把每一轮 IM 都镜像进 `bus_messages` 供 Inbox 显示，
而那个频道从没人标记已读。prod 实测后果：1,364 条永久未读，每一轮都随上下文进入 90 个
agent，署名 `lark_user_<id>` 这类伪 agent。

inbox 搬到自己的表只让**新**行不再产生。旧行还在每一个已部署的库里，而 `_unread_predicate`
正是把它们交给模型的地方 —— 所以不加这道过滤，这次改造会带着「containment 是结构性的」
的注释上线，而 90 个 agent 之后照旧被投毒。清理是部署后的手动步骤（owner 的决定），
但**注入必须在部署当天就停**，这就是过滤加在读侧的理由。

诚实的措辞是「一道盖在退役写入器遗留行上的前缀过滤」，不是结构性隔离。它可存活的原因有两个：
由 registry 推导而非手工维护（手工那版漂移过，代价是 2026-07-03 事故），以及它是临时的 ——
旧行清理完即可退休。注意 `MessageBusTrigger` 侧那道**不能**退休，它防的是重复派发。

加在 `_unread_predicate` 而不是三个读方法里：`get_unread` / `has_unread_before` /
`count_unread` 共用它正是为了不会互相矛盾 —— 「N unread (showing M)」对自己的列表说谎就是
分歧的产物。


## 2026-08-18 (二) — 私聊频道查询提为 `direct_channel_sql`

同一个三表 join 曾存在于两个文件，只差占位符方言。共享的是 **SQL 文本**而不是 execute()：
`send_to_agent` 持裸 backend（`self._db.placeholder`），`read_history` 的解析器持
AsyncDatabaseClient（`%s`），共享调用会强迫一方用错占位符 —— 那正是
[[message_bus_module.py]] 的 `_room_labels` 在 SQLite 上静默返回空的成因。

新增 `ORDER BY created_at ASC`，不是装饰：`send_to_agent` 查不到时会建频道，两个并发首发
可以都查不到、都建，此后无序的 `rows[0]` 依赖引擎，发送方和历史读取方会对「这段会话是哪个
频道」产生分歧 —— agent 拿到的是一份可信但错误的记录，而不是一个报错。


## 2026-08-18 (三) — 前缀匹配不能用 LIKE：`_` 是通配符

上一条那道过滤的第一版写的是 `NOT LIKE '<prefix>%'`,而每个前缀都以 `_` 结尾 —— `_` 在 LIKE
里是**单字符通配符**,所以 `NOT LIKE 'lark_%'` 同时排除了 `larkX_…` 和 `larky_…`,即任何以
"lark" 加任意一个字符开头的频道。SQLite 上实测:给定
(lark_oc_1, larkX_oc_2, larky_room, ch_team_1, lark),未转义的模式只留下 (ch_team_1, lark)。

当前 id 格式让这个过度匹配不可达 —— 而这恰恰是它会一直活到有人改 id 格式那天的原因,而这道
过滤决定的是**什么进入模型上下文**,过度匹配是静默的上下文丢失而不是报错。

改用 `SUBSTR(m.channel_id, 1, n) <> ?`:没有通配符语义,且前缀成为**绑定参数**,只有它的长度
进入 SQL(一个由 registry key 推出的整数)。LIKE 加 ESCAPE 也能работать,但转义字符是又一件
必须在两个方言上含义一致的东西。

因此谓词当时带参数:`_unread_params()` 与 `_unread_where()` 按同一顺序产出,三个调用方各自
拼自己的参数元组 —— 一个参数个数在调用点看不出来的谓词,正是第四个调用方会拼错的那种。

## 2026-08-18 (四) — 给自己发私聊会落进别人的频道

`direct_channel_sql` 把频道对着两条成员行做 join。两个参数相同时**两个 join 由同一条成员行
满足**，于是该 agent 所属的任意 direct 频道都匹配，`rows[0]` 是任意一条。第三轮预审执行验证：
在已有 `a↔b` 频道的库上 `send_to_agent(from="a", to="a")`，那行落进了 `a↔b`。

两个后果都比报错糟：给自己的备注被投递给了 peer b，而且经由 wake signal 给 b 起了一整轮 LLM ——
b 没有任何东西可回答。agent 把自己写成收件人是普通的模型错误（自我备注、「回复我自己」、
复制错 id），而 `message_agent` 现在的描述正是「回答别人和自己起话头是同一个动作」，等于在邀请
它发生。

读路径（`_resolve_conversation`）从一开始就拒绝了这种输入。写路径**共享了那段 SQL、却没共享
它的不变量** —— 所以不变量现在写在 `direct_channel_sql` 的 docstring 里，而不是只写在某个
调用方那儿：已经有两个调用方了，第三个否则也会踩。顺带补 `LIMIT 1`（两个调用方都只取 `rows[0]`）。


## 2026-08-18 (五) — 谓词与它的参数合成一个构造，并且是配对追加的

未读谓词与它的前缀参数原本是**两个方法**，各自读一遍 `im_channel_prefixes()`，没有任何东西把
「发出了几个占位符」和「返回了几个参数」绑在一起。这正是
[[message_bus_module.py]] `_room_labels` 的注释亲自否定过的形状（「两个独立构造分别喂计数和它的
元组，正是有人改一行就坏掉的形状」）—— 而我把它建在了那条注释隔两个文件的地方。

**合并成一个方法还不够。** 第一版 SQL 走生成器、参数取 `tuple(prefixes)`，于是在生成器里加一个
条件（跳过某个前缀、去重、`if len(pfx) > 1`）参数仍然是全量的。已实测：那个变异让整个测试套通过。
现在 clause 与 param 在**同一个循环里成对追加**，一个 `continue` 会同时丢掉两者，错位在结构上
不可能发生。

错位产生的失败是最坏的那种：`has_unread_before` 是唯一**交错**传参的调用方，多一个参数就让
`m.channel_id = ?` 收到一个前缀字符串、`m.created_at < ?` 收到一个 channel id；查询什么都不匹配，
该方法返回 False，调用方跳过 early return，`ack_read` 推进游标 —— 丢掉窗口之外所有未读。
没有异常，也不会有测试失败，除非 fixture 恰好持有正确数量的前缀。

## 2026-08-23(补)— `ack_processed` 改成前进-only(live-steering 驱动)

原 `ack_processed` 是**无条件** UPDATE last_processed_at,只在「每个调用方都传 batch 自己的 high-water」
的前提下安全(其 docstring 也这么写、并说双子 `ack_read` 才需要守卫)。live-steering 打破了这个前提:
turn 运行中 `_route_steer` 把游标推进到**比 trigger batch 更新**的被 steer 消息,而 turn 结束时自己的
ack 传的是**更旧**的 trigger high-water。无条件写会把游标**拉回**到被 steer 消息之前,让它们重新变
pending → 二次以新 turn 投递(双投)。两个 ack 还分属 poll 任务与 turn 任务、无锁,谁后写谁赢=race。

改法:`ack_processed` 抄 `ack_read` 那条 dialect-safe 的守卫 UPDATE(`{ph}` 占位符、无引用标识符、
`AND (last_processed_at IS NULL OR last_processed_at < {ph})`)。游标只前进,两个 ack 谁先谁后都无害。
同步修正 `ack_read` docstring 里「ack_processed 不需守卫」的过时说法。回归:
`test_steer_routing.test_ack_processed_only_moves_forward`(SQLite,已变异验证:去掉守卫→红) +
`test_unread_cursor_mysql.test_ack_processed_runs_on_mysql_and_only_moves_forward`(真 MySQL 方言,
与 ack_read twin 对称)。

## 2026-08-23(补2)— get_pending_messages 加 channel_id：LIMIT 落单房间

`get_pending_messages(agent_id, limit=50, channel_id=None)`：channel_id 非空时 SQL 加 `AND m.channel_id = {ph}`
（占位符、无引号标识符），params 顺序 `(agent, channel, agent)`；None 时退回原跨 channel 查询与 `(agent, agent)`。
起因（PR#352 review #4）：并发切成 lane 但取数没切——每 lane 发一条**跨全 agent** 的 `LIMIT 50` 再在 Python
里滤自己那个 channel。忙房间 A 有 >50 待处理时，房间 B 的新消息永远被挤出这 50 条、过滤成空、`return False`
不 ack，每轮白烧一个 slot——「多 team 并发」恰在它要解决的场景失效。channel scoping 让 LIMIT 落单房间。
同族先例：`has_unread_before` 的 scoped `LIMIT 1`（docstring 明写别把跨 channel backlog 拖来 Python 过滤）。
poison / stopped-tree `NOT EXISTS` 谓词与参数顺序原样保留。回归：`test_pending_channel_scope`（SQLite 饥饿场景）
+ `test_unread_cursor_mysql.test_get_pending_channel_scope_runs_on_mysql`（真 MySQL 方言）。
