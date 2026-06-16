---
code_file: src/xyz_agent_context/message_bus/message_bus_trigger.py
last_verified: 2026-06-12
stub: false
---

## 2026-06-12 — owner-relay prompt names the owner; routing keeps the user_id

`_build_prompt` gained an `owner_name=""` param. The human-facing relay line now
reads `Your owner **{owner_name or owner_user_id}** originally asked…` so the LLM
sees the owner's human name, not the opaque NetMind userSystemCode. The
`send_message_to_user_directly` routing argument on the same prompt KEEPS
`user_id="{owner_user_id}"` verbatim — the delivery tool needs the real key, so
that hex must stay. The caller resolves `owner_name` via
`UserRepository(await get_db_client()).get_display_name(owner_user_id)` (see
[[user_repository.py]]).

last_verified: 2026-06-09
stub: false
---

## 2026-06-09 — `_get_channel_info` SQL dialect bug (silent bus-delivery break)

`_get_channel_info` queried `bus_channels` with a MySQL `%s` placeholder via the
RAW backend `self._bus._db.execute(...)`. `_get_bus()` hands LocalMessageBus
`db._backend` (NOT the AsyncDatabaseClient wrapper), so the wrapper's `%s`→`?`
dialect translation never ran — SQLite threw `near "%": syntax error` on EVERY
poll cycle for any agent that had channel messages, aborting `_process_agent`
before delivery. **Symptom**: agents that were sent bus messages silently never
received them (2026-06-09: 零 created 影/镜 and messaged them; they stayed mute —
0 events — until this fix, then both processed the message and replied). Fixed
by routing through the dialect-aware `self._bus._db.get_one("bus_channels",
{...})`. Lesson: raw `backend.execute` takes SQL verbatim; only the
AsyncDatabaseClient wrapper translates dialects — never hand-write `%s` on a
path that holds a raw backend. Regression:
`tests/message_bus/test_channel_info_dialect.py` (constructs the bus with the
RAW backend to mirror production, else the wrapper hides the bug).

## 2026-05-19 — `_write_to_inbox` routed through `InboxRepository`

The hand-written `db.insert("inbox_table", ...)` referenced an `agent_id`
column that doesn't exist in `inbox_table` and an `owner_user_id` field
where the schema has `user_id`, and omitted the required `message_id`.
EC2 bus container surfaced `Unknown column 'agent_id' in 'field list'`
13 times in 3 hours on 2026-05-18.

Now we delegate to `InboxRepository.create_message` (the canonical
writer), generate a `bus_<uuid12>` message_id, and tag the row with a
new `InboxMessageType.MESSAGE_BUS` enum value. `MessageSource` is set
to `type="message_bus"`, `id=channel_id` so the inbox row traces back
to its origin channel. The previous JSON blob with original message
preview was dropped — that diagnostic data lives in `bus_messages`
already; the inbox row is a notification, not an audit copy.

## 2026-04-20 — runtime consumption via `collect_run` (Bug 2)

`_invoke_agent_runtime` now uses `collect_run`. When
`collection.is_error` is true it returns a structured `"⚠️ I couldn't
process your message right now (error_type). error_message"` string so
the sender agent sees the failure inline instead of receiving an empty
reply.

## 2026-05-12 — IM channel skip extended to telegram_ / slack_

`_process_agent()` already skipped `lark_` channels (written by `ChannelInboxWriter`
for frontend Inbox display). The same skip was missing for `telegram_` and `slack_`,
causing `MessageBusTrigger` to re-consume those messages and fire `AgentRuntime` a
second time — producing duplicate replies to the IM sender. Fixed by checking all
three prefixes together via `channel_id.startswith(("lark_", "telegram_", "slack_"))`.

# message_bus_trigger.py — MessageBus 事件驱动轮询引擎

## 为什么存在

Agent 收到消息后不能靠自己去轮询——它不知道什么时候有消息，也无法保持长连接。`MessageBusTrigger` 是代替 Agent 做轮询的"邮差"：它扫描所有频道成员、找出有待处理消息的 Agent、把消息批量投递给 AgentRuntime 处理、更新投递游标。

它替换了之前的 `MatrixTrigger`（Matrix 专用轮询），成为所有 Agent 间消息的统一投递机制。

## 上下游关系

**被谁启动**：独立进程，`uv run python -m xyz_agent_context.message_bus.message_bus_trigger` 或 `python -c "import asyncio; from xyz_agent_context.message_bus.message_bus_trigger import main; asyncio.run(main())"` 启动；Makefile 里应有对应的 `dev-message-bus` 命令（或集成到 `dev-poller`）。

**调用谁**：
- `LocalMessageBus.get_pending_messages()` 取待处理消息
- `AgentRuntime.run()` 处理消息（通过 `_invoke_runtime()`）
- `LocalMessageBus.ack_processed()` 推进游标（成功后）
- `LocalMessageBus.record_failure()` 记录失败（失败后）
- `db.insert("inbox_table", ...)` 把 Agent 的回复写入用户 inbox（通过 `_write_to_inbox()`）

## 设计决策

**自适应轮询间隔**：有消息时 `current_interval` 降到 `POLL_MIN_INTERVAL=10s`（快速处理积压），无消息时每次增加 `POLL_STEP_UP=15s`，最大到 `POLL_MAX_INTERVAL=120s`（减少空转）。这比固定间隔更高效。

**Rate Limiting**：同一 Agent 在同一频道 30 分钟内最多被激活 20 次（`RATE_LIMIT_MAX=20`, `RATE_LIMIT_WINDOW=1800s`）。超限时跳过处理但仍推进游标（消息被"丢弃"而非积压）。这防止了高频消息导致 Agent 被无限触发。

**Mention 过滤**（见 `_should_process_message()`）：频道 owner 总是被激活；非 owner 只有被 @mention 时才激活；任何人不处理自己发的消息。这三条规则是防止 Agent 间触发死循环的核心。

**并发控制**：`asyncio.Semaphore(max_workers)` 限制同时处理的 Agent 数量（默认 3），防止多个 AgentRuntime 并发运行消耗过多资源。

消息被组织成 per-channel 批次（`by_channel: Dict[str, List[BusMessage]]`），每个 channel 的消息一起投递，LLM 看到的是完整的上下文而不是碎片化的单条消息。

## Gotcha / 边界情况

`_get_bus()` 函数的注释说"LocalMessageBus is a misnomer"——它其实支持任何后端（SQLite 和 MySQL），不仅仅是本地。这个名字是历史遗留，未来可能重命名。

`_write_to_inbox()` 在 AgentRuntime 处理成功后把 Agent 回复写入 inbox——如果 Agent 的回复是空字符串（`final_output` 为空），不写入 inbox。但 `ack_processed()` 仍然会被调用，消息游标依然推进。这意味着 Agent 选择"沉默"（不回复）和"处理失败"（抛异常）在游标层面的效果是不同的：沉默会推进游标，失败会 `record_failure()`。

Rate limiter 的计数器用的是 `time.monotonic()`（进程内单调时钟），重启进程后计数器清零。如果进程崩溃后立即重启，30 分钟限额会重置，可能导致一批消息被重新处理。

## 新人易踩的坑

`_invoke_runtime()` 把所有 pending 消息组成一个 prompt（`_build_prompt(messages)`）传给 AgentRuntime，不是一条一条单独处理。这意味着 AgentRuntime 一次性看到所有积压的消息，LLM 的处理代价随消息数量线性增加。如果积压了 50 条消息，这一次 AgentRuntime 调用的 token 使用量会很高。

`trigger_extra_data={"bus_channel_id": channel_id}` 是通过 AgentRuntime 传递频道信息的方式。如果 AgentRuntime 步骤里有读取 `trigger_extra_data` 的逻辑，需要知道 key 是 `"bus_channel_id"`。
