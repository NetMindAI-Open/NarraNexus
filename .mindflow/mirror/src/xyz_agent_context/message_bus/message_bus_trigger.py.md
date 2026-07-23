---
code_file: src/xyz_agent_context/message_bus/message_bus_trigger.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — no longer its own OS process; runs under the worker supervisor

`MessageBusTrigger.start()` / `_get_bus()` are unchanged, but the trigger is no
longer launched as a standalone `-m ...message_bus_trigger` process. It is now
one supervised task inside [[run_worker_supervisor.py]] (shared event loop + DB
pool). Two consequences worth noting: (1) its flag-based sync `stop()` means the
`while self._running` loop exits at the next poll boundary (≤ `POLL_MAX_INTERVAL`
12 s) — the supervisor's cancel is the backstop; (2) it has no `ServiceAuditor`
of its own, so the supervisor's per-worker liveness snapshot (state `bus:
running/restarting`) is its FIRST L2 signal. The "独立进程" framing below is
HISTORY; `__main__` is retained as a debug entrypoint.

## 2026-07-22 — team prompt: "room files are already shared" note

Added an intro line stating every member already sees every message/file posted in THIS room
(it's in the scrollback), so there's nothing to "forward" and no claiming you did. Kills the
cosmetic "I forwarded it ✅" white lie an agent emitted when relaying — @mention is enough,
the teammate sees the same room.

## 2026-07-22 — team rule: reply-delivery forbidden, action tools allowed

Refined the group-chat tool rule again. It now distinguishes REPLY-DELIVERY functions
(forbidden — the text reply auto-posts, so `send_message_to_user_directly` /
`bus_send_message` / `bus_send_to_agent` would double-deliver) from ACTION tools (allowed):
`Read` opens a file, and **`bus_share_to_team`** publishes a file the agent produced to the
team folder (it stages bytes, does NOT post a message — the agent then mentions the returned
path in its reply). The prior blanket "no send/bus" ban blocked "share this file with the
team" and led an agent to fake a "forwarded ✅" it couldn't perform.

## 2026-07-22 — team prompt feeds recent room history (not just the @mention)

`_build_team_prompt` now takes `history` (recent scrollback via
`LocalMessageBus.get_recent_messages`, `TEAM_HISTORY_LIMIT=20`, oldest→newest) plus
`trigger_messages` (the @mentions for this agent). Before, a triggered agent only saw the
messages that @mentioned IT — so when the user posted an image @agent_1 and asked it to
relay to @agent_2, agent_2 never saw the image and the relay dissolved into a
"forward it again" back-and-forth (agent_1 even hallucinated a successful forward). Now any
triggered agent sees files/images posted by anyone in the room and Reads them directly; the
prompt points it at the latest @mention to answer. No manual relay / bus_share_to_team needed
for "discuss a shared file". `_handle_channel_batch` fetches the history in the team branch;
the retrieval anchor still uses the @mention batch only.

## 2026-07-21 — team group-chat rule: allow Read, forbid only send/bus

`_build_team_prompt`'s reply-only rule used to say "Do NOT use any tools", which made an
agent REFUSE to open a shared image/doc it was asked about (either from a `[Shared file …]`
marker or a path a teammate pasted into text). "Reply-only" is meant to prevent re-sending /
triggering teammates, NOT to block reading a file. Rule generalized: forbid
send/bus/@-trigger-to-deliver, but explicitly ALLOW read-only tools (esp. the built-in Read)
to open a file path, then reply in plain text. Applies whether or not the message carries a
structured attachment — the path often arrives as plain text.

## 2026-07-20 — prompt builders inject attachment markers + team shared-folder hint

Both `_build_prompt` (DM/owner-relay) and `_build_team_prompt` now append
`build_bus_markers(msg.attachments, …)` after each message body, so a file sent
over the bus surfaces to the recipient as the same `… use Read tool …` marker a
user upload would (see [[_bus_attachment_impl]]). `_build_team_prompt` gained
`owner_user_id` / `team_id` params (derived at the call site: owner via
`_get_agent_owner`, team_id from `channel_owner[len("team_"):]`) and, when known,
prints the team's shared-folder path (`team_shared_dir`) so teammates know where
`bus_share_to_team` drops land. Markers need no per-recipient resolution — the
stored rel_path is rebuilt against `base_working_path` into an absolute path.

## 2026-07-13 — Agent 实时层熔断器接入

`_process_agent` 顶部（信号量之前）加熔断器 `should_skip` 闸门：paused/cooling 的 agent 整体跳过，且**不消费**其 pending 消息（不 ack，留队待恢复）。这是让 bus 停止重触发坏 agent 的关键。


## 2026-07-03 — IM-channel skip prefixes now registry-driven (wechat double-dispatch)

The hand-maintained `_IM_CHANNEL_PREFIXES = ("lark_", "telegram_", "slack_")`
tuple silently drifted: wechat / narramessenger / discord were missing, so
every message on those channels was re-dispatched from their ChannelInboxWriter
history rows — a SECOND AgentRuntime run wearing the Owner-Relay peer-agent
prompt (2026-07-03 dev incident: the second run fabricated a wechat_send
context_token and sent "我已经在微信上回复你啦" platform DMs; ~$0.22 wasted
per message). New module-level `im_channel_prefixes()` derives the skip set
from `MessageSourceHandler.dedicated_trigger` registrations at call time
(import-order safe). Guarded by tests/message_bus/test_bus_channel_inbox_skip.py
(filesystem truth: every run_*_trigger.py must have a dedicated handler).

## 2026-07-02 (PR #45 review follow-up) — cooldown arms after write, error is redacted

Two fixes from automated PR review on the failure-notification change below:

1. **Cooldown timing**: `_failure_notify_cooldown[cooldown_key] = now` moved
   from *before* the `try` block to *after* `InboxRepository.create_message`
   succeeds. Previously, arming the cooldown up-front meant a transient
   inbox-write failure (DB blip, etc.) silently suppressed the real
   notification for the next `FAILURE_NOTIFY_COOLDOWN_SECONDS` — the owner
   would get NOTHING for 30 minutes even though nothing was ever written.
2. **Secret redaction**: new `_redact_error_for_owner` (static method) masks
   `sk-...`-style keys, `key=value`/`token=value` pairs, and `Bearer ...`
   headers, then truncates to `MAX_NOTIFIED_ERROR_LEN` (500 chars), before
   the error is embedded in the inbox `content`. Provider SDKs routinely
   echo the credential back in the error body (OpenAI: "Incorrect API key
   provided: sk-..."), so `str(exception)` was never safe to show verbatim
   to the owner. `_classify_error` still runs on the RAW (unredacted) error
   — it only pattern-matches keywords for the hint/cooldown category, never
   displays the string, so there's nothing to redact there.

## 2026-07-02 — permanent-failure notification (fixes NetMindAI-Open/NarraNexus#52)

`_handle_channel_batch`'s `except` block now checks the failure count right
after `record_failure()`. Once it reaches `POISON_FAILURE_THRESHOLD` (3, kept
in sync with `LocalMessageBus.get_pending_messages`'s inline `failure_count <
3` filter — see `local_bus.py.md`), `_notify_permanent_failure` writes an
`InboxMessageType.SYSTEM_NOTICE` row via the same `InboxRepository` path
`_write_to_inbox` already uses (fresh `get_db_client()`, not `self._bus._db`
— `LocalMessageBus` only holds the raw backend). Before this, a message that
hit the poison threshold just vanished from `get_pending_messages` forever
with zero owner-facing signal — the exact silent-failure bug reported in
NetMindAI-Open/NarraNexus#52 (broken OpenAI provider → every IM/bus message
dropped after 3 failed `_invoke_runtime` calls, no visibility, no recovery).

De-duplicated per `f"{agent_id}:{error_category}"` with a 30-minute cooldown
(`_failure_notify_cooldown`, same in-memory / per-process pattern as
`_rate_counters` — resets on restart, an accepted tradeoff) so a batch of
messages failing for one root cause (e.g. every pending message for an agent
whose provider key just broke) writes at most one inbox row, not one per
message. `_classify_error` does a coarse substring match on the stringified
error for `"credential"` / `"api_key"` / `"401"` / `"provider"` / etc.
markers — this only changes the hint text ("check the agent's LLM provider
configuration…" vs. a generic "check recent activity"), not any retry or
delivery behavior. The recovery half — clearing a failure record so
`get_pending_messages` picks the message back up — lives in
`backend/routes/agents_bus_failures.py`, not in this file (this file only
detects + reports the permanent failure).

## 2026-06-23 (PM) — prompt names the live roster, forbids off-channel @mentions

`_build_team_prompt` now states the current channel members explicitly and adds
a rule: only @mention someone in that list; anyone named in history but not a
member has left / was never here. Fixes agents @mentioning a non-member (e.g.
Nex @rabbit when rabbit isn't in the channel). Delivery was already safe
(`_extract_team_mentions` only resolves to real members) — this stops the agent
from *writing* the dead mention in the first place.

## 2026-06-23 — team group-chat branch + cascade cap + faster polling + cursor fix

`_handle_channel_batch` now branches on `channel_owner.startswith("team_")` (a
team group-chat room — see `teams.py.md`). **Team branch**: a group-chat prompt
(`_build_team_prompt`) that forbids tools / process-narration and just talks; the
agent's plain reply is posted BACK into the channel as that agent, with
@mentions parsed (`_extract_team_mentions`, @Name/@all → member ids / `@everyone`)
so a hand-off pulls teammates in. Every non-team channel (peer DM, IM bridges)
keeps the original owner-relay + inbox path untouched. **Cascade cap**:
`_team_cascade_depth` counts consecutive trailing agent (non-`usr_`) messages;
past `MAX_TEAM_AGENT_HOPS` (4) the reply's @mentions are dropped so two agents
can't @ each other forever (a human message resets the chain). **Latency**:
adaptive poll bounds lowered to MIN 3s / MAX 12s (was 10/120) so a reply lands
quickly after idle.

Bug fix (shared, all bus delivery): the cursor-advance calls used
`str(latest.created_at)`. When `created_at` is an auto-parsed `datetime`, `str()`
gives space-format `"YYYY-MM-DD HH:MM:SS+00:00"` while `created_at` is isoformat
`"…T…+00:00"`; lexicographic compare in `get_pending_messages` ('T' > ' ') then
makes every newer message look unprocessed → the agent loops. Dropped the
`str()` wraps; canonicalisation now lives in `local_bus.ack_processed`.

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
- `InboxRepository.create_message()`（`message_type=SYSTEM_NOTICE`）把永久失败通知写入 owner 的 inbox（通过 `_notify_permanent_failure()`，当某条消息的失败次数达到 `POISON_FAILURE_THRESHOLD` 时触发；见下方 2026-07-02 changelog）。这个失败记录的读取/清除（重试恢复路径）在 `backend/routes/agents_bus_failures.py` 里，**不在**本文件——本文件只负责检测和上报。

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

## 2026-07-07 — 凭据分类 + 脱敏抽到 agent_framework/llm_failure

`_classify_error` / `_redact_error_for_owner` 现委托到共享的
`agent_framework.llm_failure`（`is_credential_error` / `redact_secrets`）。行为不变
（`MAX_NOTIFIED_ERROR_LEN` 仍 500），只是让 bus / narrative / Step-5 hooks 三条后台
路径用同一套判断（去重，铁律 #8）。原本散落此处的 markers / _SECRET_* 正则已移除。

## 2026-07-22 — team runs mirror live activity

The team branch of `_handle_channel_batch` wraps the run: `mark_running` before, an opt-in
`on_progress` (via `_make_activity_progress`, throttled — writes on phase change or ~2s
heartbeat) passed through `_invoke_runtime`→`run_and_collect`→`collect_run`, and `mark_idle`
in a `finally`. Populates [[_bus_activity]] so the team UI shows running/phase/elapsed. Only
team channels; DM/IM/Job paths pass `on_progress=None` (unchanged).
