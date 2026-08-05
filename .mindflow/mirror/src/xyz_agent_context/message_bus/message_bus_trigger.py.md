---
code_file: src/xyz_agent_context/message_bus/message_bus_trigger.py
last_verified: 2026-08-05
stub: false
---

## 2026-08-04 — team 房标记进 trigger_extra_data（bus_team_room）

team 房与普通 bus 轮同为 working_source=MESSAGE_BUS，但交付契约相反
（前者纯文本自动上墙、prompt 禁投递工具；后者只有工具调用才送达）。
`_handle_channel_batch` 的 team 分支把 `team_room=is_team` 传入
`_invoke_runtime`，后者盖进 `trigger_extra_data["bus_team_room"]` →
`ctx_data.extra_data`，供 [[message_bus_module]] 的 expressive 声明门控
（team 房不广告 bus 工具，避免自动上墙 + 工具调用双发）。与
include_monologue 同形的两端钉死（tests/message_bus/test_team_room_marker.py）。

## 2026-08-03 — turn-source 章记录的是「轮次种类」,不是「这条消息在问还是在答」

Review round 3 抓到的复发:`sender_turn_source == "message_bus"` 被当成
「这是回复」的充分条件,但 bus 轮次里也能**提问**——Owner Relay 指令自己就
教发起方"有澄清问题用 bus_send_to_agent 追问"(路径 A),回答方也可能为了
组织答案再问第三个 agent C(路径 B)。两条路径上收件方都会误判成
Owner Relay,P1 原样复发。

修成两半:
1. **发送侧,按「这一条发给谁」盖章(不是按整轮)**:trigger 把分类器判定
   (`i_started`)转成本轮的**差事作用域**——`_invoke_runtime` 用
   `sender_agent_id`/`channel_id` 填
   `trigger_extra_data["bus_errand_peer" / "bus_errand_channel"]`,经
   [[context_runtime]] 落到 MCP identity header/bearer;工具侧
   `_send_turn_source`(见 [[_message_bus_mcp_tools]])把**本条 send 的目标**
   与作用域比对,只有打向差事对手的那条才盖 [[hook_schema]] 的
   `BUS_ERRAND_TURN_SOURCE`。
2. **收件侧兜底** `_i_have_errand_in_channel`:全批都是 plain
   "message_bus" 章时,只有当**我自己**在此 channel 有过非
   "message_bus" 章(或 legacy NULL)的发言——即我真的问过——才算
   「对我差事的回复」;从没问过的 agent 不可能被欠答案(路径 B:
   回答轮次 fan-out 给 C)。判据已下推进 SQL(`SELECT 1 … IS NULL OR <>
   … LIMIT 1`):这条查询跑在最常见的触发路径上,而 DM channel 被对称复用、
   永不新建,客户端全量扫描会随 agent 对的寿命单调增长。DB 失败 →
   Owner Relay,与外层同向降级。

### 为什么第 1 步必须 per-send —— 整轮盖章曾让 P1 换个位置复发

第一版把整轮盖成 `message_bus_errand`。但**一轮不只包含差事**:
`MessageBusModule.hook_data_gathering` 每轮调 `bus.get_unread`,而
[[local_bus]] 的实现是跨**所有** channel JOIN 成员表,把别的 channel 的未读
注进 `extra_data`;模块提示词紧接着**要求**回答它们(「A question is never
ping-pong — answer it」)。于是「A 在差事延续轮次里顺手回答了 C 在另一个
channel 的提问」是**平台自己注入 + 自己要求**的路径,不是角落:C 收到的回答
被盖成提问 → C 不再向自己 owner 回报 → 正是本 PR 要修的那个失败,换了个座位
(2026-08-03 review round 4)。只有 send 现场知道自己打给谁,所以只有 send
现场能定章。

### 已接受并写进 docstring 的残余洞(**不是**"风险已穷举")

1. **旧差事**:我曾在此 channel 跑过差事,之后同伴**从回答轮次**问我一个全新
   问题(无任何提示词引导这条路)→ 旧差事行仍投 Owner Relay 票。轮次章表达
   不了 per-message 的问/答意图。退化 = 修复前行为。
2. **同一 DM channel 里双向差事同时在飞**:DM channel 对称复用,若差事对手
   **也**问了我们什么、而我们在差事延续轮次里回答了他,这条回答打的正是差事
   作用域 → 盖成提问 → 对方去"回复同伴"而不向自己 owner 回报。要踩到得两边
   owner 同时各派了一个指向对方的差事。**这是我们主动选的方向**:另一条路
   (整轮盖章)破的是平台**自己引导**的场景(跨 channel 未读每轮注入 + 提示词
   要求回答),触发频率高得多。
3. **群 channel 当差事 channel**:作用域也按 channel 匹配,所以发进「恰好是
   差事 channel 的群」会把每个成员的那份都盖成提问。bus 差事跑在自动建的 DM
   channel 上,要踩到得手工建群并拿它当差事 channel。
4. **大小写/手写章**:差事行检查用 SQL `<>` 精确比对,非我方写入器写的、大小写
   不同的行会被算作差事行 → Owner Relay,与其它降级同向。

彻底关掉 1、2 需要**逐条声明意图**(发送方每条说明"这是问还是答")——review
提过、我们**没有采纳**:那会把一个正确性关键位重新压在模型听话上(铁律 #15:
机器可知的事实不能取决于用户选了哪个模型)。将来要做,默认值必须是**推导**
出来的,不能靠假设。

## 2026-08-01 — Owner Relay 只发给「发起方」,被问的一方改成「回复同伴」

P1 段 06 的**真正根因**,靠真机跑出来的(单测抓不到):`_build_prompt` 只要
`owner_user_id` 存在就追加 `## Owner Relay — REQUIRED`——而它总是存在。
于是**被问的那一方**也被告知"你的 owner 当初让你联系这个 peer,他正在
聊天里等答案"。对收件方这是**假话**:它的 owner 什么都没问。

现场后果(连跑 3 次复现):小雀替 TC 转达问题 → 羽书 收到假的 Owner Relay
→ 调 `send_message_to_user_directly` 回给自己 owner,并认定差事已了
(「未回复小雀 — 她是转发…按 Reply Discipline」)→ 小雀(已向用户承诺回报)
永远等不到回复。**模型是在照做,是 prompt 在骗它。**

修法(2026-08-04 定稿):按**消息上记录的事实**选指令,不再靠 channel 排序推断。

发送方在 `bus_send_to_agent` 时把**自己这一轮的种类**写到消息上
(`bus_messages.sender_turn_source`):owner 面的 turn(chat/job/…)=我在跑
差事、这条是**提问**;`message_bus` turn = 我本来就在答同伴、这条是**回复**。
触发侧读进来那批消息的这个字段即可,零历史查询。

**两次靠 channel 排序推断都错了**,记下来别再试:
1. 「我在这个 channel 说过话吗」—— 被问方回复一次之后就"说过话"了,**追问**
   会翻回 Owner Relay,bug 原样复发。
2. 「谁开的场」—— `send_to_agent` 找 DM channel 是**对称查找 + 复用**
   (local_bus:245),所以 A 一旦 DM 过 B,opener 永远是 A;此后 B 反向跑差事
   问 A 时**两边都判错**:A 拿到 Owner Relay(把 B 的问题转给自己 owner,
   P1 原样复现),B 收到回复时被告知"你 owner 没在等"(不回报)。**这不是
   罕见退化,是那一对 agent 的反向永久失效** —— 而且第 2 点里 B 那一步
   是我这次改动**引入的回归**(改之前它会拿到 Owner Relay 并正确回报)。
   review 抓出来的。

降级顺序:字段为空(存量行)且**我从没在此 channel 发过言** → 显然是
被问方;否则 → Owner Relay(2026-08-01 前的行为)。**注意这个降级分支本身
就是上面第 1 种错法**,所以它只能是兜底、不能是常态:第一版 turn source
只走显式 header 而 codex 不转发,codex 提问方于是恒走降级,追问从第 2 个
问题起就错 —— 已通过让 turn source 搭 bearer 修掉(见 [[_mcp_identity]])。
DB 异常同样回落 Owner Relay:错 relay 只是体验噪音,错误压掉 Owner Relay
会让 2026-06 那个静默失败复活。

`_build_prompt` 的 `i_started_this_exchange` 改成**关键字必填**:它决定给
agent 两条互相矛盾的指令里的哪一条,漏传不该静默继承 Owner Relay。

真机验证(第 4 次):羽书 改为在 bus 上回复并附状态,还自己诊断了前三次
「我之前三次都直接回复了 TC…但 TC 似乎没看到」;小雀 随后被触发并
「已将羽书的回复完整转达给 TC」,同时正确地没有再 ping-pong 回去。
整条链路(发问 → 对方答 → 回报用户)闭合。

## 2026-07-31 — _get_agent_owner 委托 AgentRepository.resolve_owner

行为不变（异常仍回 '' + warn），实现收敛到 repository seam。

## 2026-07-31 — team reply rows are stamped with their turn's event_id

`_invoke_runtime` now returns `(response_text, event_id)` (from
`RunCollection.event_id`; None if the run died before Step 0 — including the
error-string path, which still carries whatever id Step 0 produced). The team
branch passes it to `bus.send_message(event_id=...)` so every posted reply
row references the turn that produced it — the per-MESSAGE handle behind the
transcript's reasoning disclosure, complementing `note_event_id`'s
per-MEMBER latest-turn binding on the activity row.

## 2026-07-30 — 只有 team room 分支对 collect_run 开 `include_monologue`

team room 的 prompt（`_build_team_prompt`）明说「你的明文会自动上墙」，所以
NexusPower 独白在这条分支并入收集文本（`include_monologue=is_team`）；peer
DM→收件箱分支的 prompt 让 agent 用 `send_message_to_user_directly` 送达、
从未承诺明文落库，独白保持私密（否则 owner 会同时收到润色直发 + 一条原始
独白的收件箱条目）。语义见 [[run_collector]] 同日条目。

## 2026-07-28 — the poll loop stops being a single point of failure, and reports work

Two defects, one incident. Between 2026-07-27 00:17 and 2026-07-28 09:06 the bus
processed **zero** messages for **every** user — 33 hours — with no exception, no
restart, and a liveness signal that read healthy throughout. A container restart
drained the backlog in 0.1 s.

**Why it froze.** `_poll_cycle` did `asyncio.gather` over every agent that was a
member of any channel (364 on prod) and awaited all of them. Inside,
`_process_agent` takes one of `MAX_WORKERS` (3) semaphore slots and calls
`_invoke_runtime`, which by design has no timeout (binding rule #14). So three
wedged provider connections exhaust the pool, the gather never returns, and the
loop stops — for everyone, not just those three agents.

The cycle now **dispatches and moves on**: `_dispatch` spawns a supervised task
per agent (`_InFlight`, paired `add_done_callback` per incident lesson #2) and
the loop immediately continues. A stuck turn holds its own task and its own slot;
the loop keeps cycling and can still serve everyone else.

**Why nobody noticed.** This was the only long-running worker without its own
`ServiceAuditor`. The supervisor's `bus: running` is set once at start and never
updated — L1, not L2 (see [[run_worker_supervisor]], corrected in the same
change). Now `ServiceAuditor("message_bus_trigger")` emits started/stopped/error
plus a heartbeat carrying `liveness_snapshot()`, whose whole job is to make the
two failure modes distinguishable in SQL:

| symptom in `service_audit` | meaning |
|---|---|
| `cycles` frozen | the loop itself is wedged |
| `cycles` rising, `dispatched_total` frozen, `candidates` > 0 | loop fine, nothing can start |
| `running == max_workers` and `waiting` > 0, sustained | the worker pool is the bottleneck |
| `longest_running_agent` / `_s` | *who* is holding a slot |

`longest_running_*` is **diagnostic only**. Nothing here force-stops a turn: a
multi-hour run is a legitimate workload, and the fault being guarded is our loop
dying, not an agent taking its time (binding rule #14).

**Scan cost.** `_agents_with_pending()` replaces "every channel member" with one
query for agents that actually have a message past their cursor. Deliberately
over-inclusive: it skips the @mention filter because an un-addressed member is
precisely who must be dispatched so `_process_agent` can ack and advance its
cursor — filter them here and cursors freeze and the scan never converges.

`stop()` also sets an event so the loop leaves its interval sleep at once instead
of waiting out up to `POLL_MAX_INTERVAL`, and cancels in-flight dispatches so the
loop that owns them doesn't leak them.

## 2026-07-30 — team turns bind their event_id onto the activity row

The team branch also hands `act.note_event_id` to `_invoke_runtime` as
`on_event_id`; `collect_run` fires it once when the Step-0 progress message
surfaces the turn's events-row id. Non-team invocations pass nothing — the
parameter defaults to None end to end.

## 2026-07-28 — team activity scoped by `turn()`

The team branch's three-part activity dance (mark_running up front, a bespoke
throttled `_make_activity_progress` closure, mark_idle in a `finally` wrapped
around only the runtime call) collapsed into
`async with _bus_activity.turn(...) as act` over an `AsyncExitStack`, with
`act.on_progress` handed to the runtime. The scope now covers the whole
handled batch rather than just `_invoke_runtime`, and the timer heartbeat that
keeps the row live during a silent stretch belongs to `turn()` — see
[[_bus_activity]]. `_make_activity_progress` is gone.

`POISON_FAILURE_THRESHOLD` is now imported from [[local_bus]] instead of being
a hand-synced copy.


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

> **Both numbered points above were superseded on 2026-07-28 — see the entry at
> the top of this file.** (1) `stop()` now wakes the loop immediately; (2) the
> supervisor snapshot was never L2 — it is L1, and it is exactly what let a
> 33-hour outage look healthy.

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
`backend/routes/agents/bus_failures.py`, not in this file (this file only
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
- `InboxRepository.create_message()`（`message_type=SYSTEM_NOTICE`）把永久失败通知写入 owner 的 inbox（通过 `_notify_permanent_failure()`，当某条消息的失败次数达到 `POISON_FAILURE_THRESHOLD` 时触发；见下方 2026-07-02 changelog）。这个失败记录的读取/清除（重试恢复路径）在 `backend/routes/agents/bus_failures.py` 里，**不在**本文件——本文件只负责检测和上报。

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

## 2026-07-07 — 凭据分类 + 脱敏抽到 agent_framework/llm/failure

`_classify_error` / `_redact_error_for_owner` 现委托到共享的
`agent_framework.llm.failure`（`is_credential_error` / `redact_secrets`）。行为不变
（`MAX_NOTIFIED_ERROR_LEN` 仍 500），只是让 bus / narrative / Step-5 hooks 三条后台
路径用同一套判断（去重，铁律 #8）。原本散落此处的 markers / _SECRET_* 正则已移除。

## 2026-07-22 — team runs mirror live activity

The team branch of `_handle_channel_batch` wraps the run: `mark_running` before, an opt-in
`on_progress` (via `_make_activity_progress`, throttled — writes on phase change or ~2s
heartbeat) passed through `_invoke_runtime`→`run_and_collect`→`collect_run`, and `mark_idle`
in a `finally`. Populates [[_bus_activity]] so the team UI shows running/phase/elapsed. Only
team channels; DM/IM/Job paths pass `on_progress=None` (unchanged).

## 2026-08-05 — [bus-timing]：每 hop 一条计时线

与 runtime 的 [turn-timing] 配套：`_handle_channel_batch` 成功路径落
`[bus-timing] agent= channel= team= batch= queue_wait_s= turn_s= hop_s=`。
queue_wait=消息入库→本次 dispatch（受自适应轮询 3-12s 约束）,turn=runtime
调用,hop=入库→送达完成（team 房含 post 回房;DM 的 bus_send 在 turn 内,
turn 即覆盖）。created_at 解析复用 run_recorder.parse_db_utc（datetime/ISO
字符串都吃,缺失回落 -1.0 不炸）。失败路径不发计时线。
测试:tests/message_bus/test_bus_hop_timing.py。

### 2026-08-05 R2（review 修正）：解析器归一 + hop 语义一致 + 观测不进 try

- 时间戳解析改用**包内已有**的 `local_bus._as_utc`（同一张表同一字段两个
  解析器是下次改语义只改一边的入口）,删掉对 agent_runtime.run_recorder 的
  跨包依赖。
- created_at 缺失时 `hop_s` 同发 -1.0（R1 会静默换定义成 dispatch→delivered,
  混进 p50/p99 把分布拉低）;一个过滤条件摘掉全部不完整行。
- 新增 `oldest_wait_s`：queue_wait 量的是**触发消息**（批次里最新一条）,是
  用户等待的下界;oldest 是上界。batch>1 时两者并读。
- `[bus-timing]` 行移出 try——观测代码不该有能力把已送达已 ack 的消息记成
  投递失败并推进毒药计数。成功（_hop_done）才发。

### 2026-08-05 R3：deliver 差值语义回写

hop_s − queue_wait_s − turn_s = 投递段（runtime 返回后的 ack + 上墙/写
inbox）——是有意义的第四个量,不是误差（R2 重写注释时丢了这句,review 指出,
已回写进代码注释）。
