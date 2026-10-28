---
code_file: plugins/builtin.chat/src/narranexus_plugins/chat_module/chat_module.py
last_verified: 2026-09-10
---

## 2026-09-10 — no-reply markers are a cross-repo contract

The two placeholders persist_turn writes for a blank turn,
`(Interrupted by user)` and `(Agent decided no response needed)`, have a
byte-identical frontend copy in `frontend/src/lib/turnMarkers.ts`;
`localizeTurnMarker` there maps them to localized labels by exact match. Comments at
both places the literals appear (the persist_turn assignment and the
no-reply return of the response extractor) now point at that file: changing the wording
here without changing it there makes the UI show the raw English marker.
Both sides pin the literals in tests (`tests/chat_module/test_blank_reply_guards.py`,
`MessageBubble.turnMarkers.test.tsx`).

## 2026-08-21 — 活动摘要的 bus-source 判定改用共享常量

`_working_source_summary` 里 `if working_source in ("message_bus", "a2a")` 的硬编码字面量
换成 `hook_schema.BUS_PRODUCED_SOURCES`(值不变,纯去重)。此前这对字符串在枚举本体、这里、
以及 Activity Log 路由三处各写一份;统一到单一来源后,新增一个 bus 侧 transport 只改一处、
所有消费方一起生效,避免"漏改一处 → 活动静默不显示"这类无声 bug。行为无变化。

## 2026-08-21 — hook 兜底 prepend:sibling 抑制 + 不再盖 event_id(深圳复测 B2)

`persist_turn` 的首轮问候 prepend 补两刀:
① 先查 [[_chat_writes]]`.agent_chat_has_history`——该 (agent,user) 在
别的 instance 已有历史就不种(新 narrative 的空 instance 不再被当
「首次接触」重新问候;检查失败 fail-open,最坏是旧行为的一次重种)。
② `build_bootstrap_greeting_row(...)` 不再传 `event_id`——问候不属于
任何 run,盖当次 run 的 id 会让它与本轮真实回复共享前端时间线的
(role, event_id) 去重身份(B2 的另一半)。seed 路径本就不盖,两个
写入方从此一致。

## 2026-08-20 — bootstrap 问候 prepend 改用共享行构造器

`persist_turn` 里首轮 prepend 问候语那段,行结构改成调用 [[_chat_writes]] 的
`build_bootstrap_greeting_row(greeting, base_dt, instance_id, event_id=...)`,不再内联手写。
「问候行长什么样 + 时间戳规则」现在只有一处定义,和 `step_1` 的开局 seed 共用同一构造器,不会
两处漂移。guard 仍是 `len(messages)==0 and bootstrap_active`——seed 已经先写过时它自然跳过(不双写)。
顺带:hook 路径的时间戳也纳入了 `_chat_writes` 的 naive→aware-UTC 归一(今天 `event` 是进程内
aware 对象、无可观察差异,但不再依赖这个前提);内联版用的 `from datetime import timedelta`
随之成死 import,已删除。


## 2026-08-19 — plain-text（巡查）回合不声明 owner 工具

`expressive_tools` 在 `BUS_PLAIN_TEXT_TURN_EXTRA_KEY` 为真时返回 `[]`：巡查回合靠说话投递，声明 `notify_owner` 会让回复提醒渲染成「reply with notify_owner」，与巡查 prompt「写纯文本、别调工具」互斥。只撤**声明**，schema 仍在桌上（`disallowed_tools` 不变，中途升级给 owner 合法）。同类修复见 [[channel_module_base]]。

## 2026-08-17 — 每轮桌上只有一个 owner 工具，另一个从上下文里拿掉

`expressive_tools` 现在按轮次返回 `reply_owner`（owner 自己的聊天轮）或
`notify_owner`（其余全部），**永远只有一个**；新增的 `disallowed_tools` 把另一个的
schema 也从模型上下文里移走。

只声明不移除是不够的：声明只决定回复提醒**念**哪个名字，schema 是另一条路进上下文的。
两个工具挂着相反的纪律（一个是「几乎每轮都该用」，一个是「默认别用」），模型同时看得见
就得自己挑，而挑错不是免费的。prod 实测：两个明写 "Do NOT call" 的工具被调用了 615 次
——散文劝阻不管用，这是这次改造的直接依据。

`_is_owner_chat_turn` 在**没有 working_source** 时返回 True。两个猜错方向不对等：把
bus 轮猜成 owner 聊天只是语气偏了；把 owner 聊天猜成 `notify_owner`，桌上那条纪律写着
「默认别用」，用户刚说完话可能一个字都收不到。

`_split_user_visible_response` 的分流改用 `is_owner_tool`（见 [[message_source_handler]]）。
只匹配其中一个名字，会让另一条表面上的 owner 回复全部落进 IM 桶，在聊天面板里渲染成
「Background activity」。


## 2026-08-10 (PR-10) — create_mcp_server 调用简化

`create_mcp_server` 改调 `create_chat_mcp_server(self.port)`（去掉 `ChatModule.get_mcp_db_client` 实参）——get_chat_history 迁 seam 后工厂不再需要 db-client 函数。见 [[_chat_mcp_tools]]。


## 2026-08-05 — 删掉两个死的 `agent_messages` 家族 import

`AgentMessageRepository` 和 `MessageSourceType` 在本文件里从未被使用，删除。
它们是一条误导性的线索：`agent_messages` 表**没有写入者、恒为 0 行**（墓碑表，
见 [[agent_message_repository]] 2026-08-05），ChatModule 从来没往那里写过。
本模块的聊天正文一直写在 `instance_json_format_memory_chat`（`persist_turn`
→ `event_memory_module.add_instance_json_format_memory`，按 chat instance_id
分片），也正是 `/simple-chat-history` 回放的那份。0802【对话时序错乱】的分析
被这条线索带偏过一次，故在 import 处留了一段注释钉住事实。

## 2026-08-04 — assistant_content 守卫 strip 化 + NO-REPLY 日志字段更名

主路径占位守卫从 `if not assistant_content` 改为 `.strip()` 判空，与
silent-batch 分支（早已 `.strip()`）口径对齐。注意：这是**第二道网**
——第一道在 extract_reply_text 的空白守卫（剥空的 part 进不了 split
的 join），当前代码里本分支对纯空白实际够不到，保留作跨层纵深防御，
防未来 extractor 路径回归。interrupted 分支语义不变——空白+打断落
"(Interrupted by user)"。[NO-REPLY] 日志里误导的 `final_output_empty`
字段更名 `placeholder_reply`（它判的是占位文案，与 io_data.final_output
无关），文案同时覆盖「没调回复工具」与「调了但剥空」两种情形。

## 2026-08-04 (review 三) — 背景轮持久化二分：[DELIVERED-BG] vs [NO-REPLY-BG]

review round 2 抓到：owner-visible 拆分后，bus 名单扩容失去活消费方，
真交付的 bus 轮仍被打 [NO-REPLY-BG]，「要不要兜底」的度量继续失真。
新增 `_delivered_to_origin(working_source, responses)`（吃 handler 的
**全量** user_reply_tool_names，与 owner-visible 消费方分工明确），
else 分支据此二分：交付过 → [DELIVERED-BG] 日志 + activity meta
`delivered_to_origin=True`；真沉默才是 [NO-REPLY-BG]。
`_build_activity_summary` 增 delivered_to_origin 参数，bus/a2a 文案
诚实化——旧版无条件 "Replied to X" 在 8/1 场景说的是没发生过的回复，
现在只有真交付才说 Replied，沉默说 "(no reply sent)"。
同批：working_source_matches/WorkingSource import 上提；no-match 日志
改用 handler.effective_owner_visible_names（回落规则单点化）。

## 2026-08-04 (review 修正) — user-visible split 改用 owner-visible 谓词

`_split_user_visible_response` 的提取由 `extract_reply_text` 改为
`extract_owner_visible_text`：bus-only 交付轮次回到 activity 行持久化
（不再把 agent↔agent 交换写成 owner 可见的对话对）；IM 渠道经 None 回落
行为逐字节不变。no-match 日志改说 "no owner-visible reply tool matched"
并打印生效的 owner 名单（原名单语义已拆分，照旧打印会误导排查）。

## 2026-08-04 — claims_source(CHAT) + 声明语义更新

origin-first 排序落地后，本模块声明仅在 CHAT 轮凭 origin 排第一；
非 chat 轮凭 priority 1 紧随来源模块之后——仍全场在列（Owner Relay
合法经它交付），但不再是所有轮次的默认。docstring 同步改口。

## 2026-08-04 — expressive_tools 补上 ctx_data(review 抓漏)

签名扫尾漏了本类:基类/调用点已改带参,本覆写仍是 (self)-only →
每次调用 TypeError → 收集点 fail-open 吞掉 → owner chat 默认回复工具
声明静默清零(NexusPower 路径致哑)。已补参;并新增 module_registry 全量
签名守卫测试 + 收集点对 TypeError 单独 logger.error(签名漂移是接线
bug,不许长得像"某模块声明崩了,无所谓")。
## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

覆写 `expressive_tools()`:从 `mcp_server().server_name` 派生全名
(不写字面量——改 server 名静默失效 = 把 agent 变哑,正是本 PR 删掉的失败
模式;测试钉在真实注册面上)。Chat priority=1 → **(priority, module_class)
全序排后首位** → 成为本回合默认回复工具(NexusPower constitution 的例子名)。

## 2026-07-30 — 被打断 turn 的持久化标记

`persist_turn` 读 `params.io_data.interrupted`:无回复时占位文案改为
"(Interrupted by user)"(它不是 no_response,IM 源也照常写行),assistant
meta_data 加 `interrupted: true`。此前用户 Stop 的 turn 根本到不了这里
(runtime 在 Step 4 前 raise)——见 [[agent_runtime]] 2026-07-30 条目。

## 2026-07-09 — `_synthesize_attachment_markers` collapses to a wrapper

Historical-turn marker synthesis (call sites at `chat_module.py:508` and `:889`) now delegates to `Attachment.markers_from_dicts` (see [[attachment_schema.py]] 2026-07-09). The wrapper is kept so the two call sites don't have to import the schema helper directly, but the implementation lives in one place. Current-turn marker synthesis now happens in [[context_runtime.py]] `build_input_for_framework` — same underlying helper, so agent behaviour is uniform across current vs historical attachments.

Malformed attachment dicts are no longer silently dropped: the schema helper emits a WARNING (`skipping malformed attachment dict: <type>: <msg>`). Silent drops would recreate the "agent claims no file received" class of failure the 2026-07-09 fix addresses.

## 2026-07-02 — silent-batch write path in `persist_turn`

`persist_turn` now branches at the top: if
`params.ctx_data.extra_data["batch_messages"]` is a non-empty list, we
skip the normal single-turn write (user + assistant) and instead
append ONE `user` row per batch entry, with each row's own
`event_id / timestamp / sender_id / sender_name / attachments` and
`meta_data.silent=True`. NO assistant row is written — silent runs
(see [[agent_runtime.py]] `silent=True`) skip step_3 entirely, so
there's nothing agent-authored to persist.

Consumer: [[channel_trigger_base]]'s
`_build_and_run_agent_silent_batch` for IM group non-@ ingestion and
reconnect backfill. The write is the only place per-message
attribution survives — GeneralMemoryModule's observation extraction
operates on the merged input_content and does not preserve
per-sender identity, so chat_history is what carries "who said what
in the group while agent was silent" forward. Empty-content-and-no-
attachment entries are skipped defensively (matches the
_process_message guard for the non-batch path).

## 2026-07-03 — _build_activity_summary says WHAT happened, not the source

Was "Executed a background job" / "Background activity (wechat)". The Inner
Thoughts card now badges the source with its own colour + name, so echoing
"(wechat)" is noise. The summary uses the channel_tag the IM triggers attach
(sender_name / room_name): job → "Ran a scheduled job"; message_bus/a2a →
"Replied to {who}" or "Handled a peer-agent message"; IM with a sender →
"Handled a message from {who}"; otherwise "Handled a background activity".
Never echoes the raw working_source token. Guarded by
tests/chat_module/test_activity_summary.py.

## 2026-06-08 — memory_chat mirror write removed

The `_feed_chat_to_engine` path that wrote a `memory_chat` search index per turn is deleted — conversation search is now the interaction index written in [[step_4_persist_results]] (chat+event merged). ChatModule still owns the OPERATIONAL chat history (`instance_json_format_memory_chat`) for injection and Fetch (`get_chat_history`); only the redundant search-mirror write is gone.

## 2026-05-29 — decoupled from sibling EventMemoryModule (iron rule #3)

`self.event_memory_module` is now an `EventMemoryRepository` (from the
repository layer), not the former sibling `EventMemoryModule`. The
attribute name is kept for test compatibility; only the underlying class
moved (module/ → repository/). ChatModule no longer imports any sibling
Module. The EverMemOS-based long-term semantic memory it used to mention
is gone (removed 2026-05-29); long-term memory is now the current
narrative's full history surfaced as the unified timeline.

## 2026-05-25 — Accept any `helper_llm_*` reply_via tag

The reply_via copy loop in `persist_turn` was strict-equality on
`"helper_llm_fallback"`. As of the fallback-context redesign the synthetic
ProgressMessage tag is one of `helper_llm_no_reply` (clean turn, agent
forgot to call send_message) or `helper_llm_after_error` (loop crashed
mid-stream and helper_llm wrote a recovery reply). Persistence now
copies any `helper_llm_*` tag onto `meta_data.reply_via`, so the UI can
render `no_reply` as an info badge and `after_error` as a warning badge.
T5 builds on this to relax the fatal-detection branch so a recovered
turn is persisted as a normal user+assistant pair rather than a failed
user-only row.

## 2026-05-20 — conversation write moved to synchronous `persist_turn`

The conversation-row write (build user+assistant messages → `add_instance_json_format_memory`)
moved OUT of `after_turn` into the new SYNCHRONOUS `persist_turn`
(see [[base.py]] / [[hook_manager.py]] / [[agent_runtime.py]] Step 4.6). Reason: the
old write lived in the backgrounded hook, which lags 3–19s; a user replying instantly
raced it and the next turn read history missing the exchange ("short-reply amnesia").
`persist_turn` now writes it in-request, before the WS closes. What stays in the
background `after_turn` is ONLY the heavy Part-B embedding
(`_embed_message_pair`), which re-locates this turn's user+assistant pair by `event_id`
(robust to a later turn appending more messages before the background task runs).

## 2026-05-20 (Fix #2 hotfix) — resolve cross-narrative tag via links table

P1 originally read each cross-narrative chat instance's narrative off
`instance.linked_narrative_ids[0]`. That was wrong and crashed in prod:
`InstanceRepository.get_chat_instances_by_user` returns BASE
`ModuleInstanceRecord` objects, which have NO `linked_narrative_ids` attribute
(that field lives only on the `ModuleInstance` subclass in [[instance_schema.py]]
and isn't populated by the SELECT anyway). The access raised `AttributeError`,
the whole short-term load was swallowed by the try/except → the agent saw zero
cross-narrative history → "amnesia". New
`_resolve_instance_narratives(instance_ids)` maps each instance_id → narrative_id
via `InstanceNarrativeLinkRepository.get_narratives_for_instance`
(`instance_narrative_links`), used by BOTH `_load_short_term_memory` and
`_load_recent_actions`. The unit tests now build REAL base records (no attr) +
mock the link repo, so they fail the way prod did if anyone reads the attribute
off the record again. (Below, "the source instance's `linked_narrative_ids[0]`"
is superseded by this resolution.)

## 2026-05-20 (Fix #2 P1) — unified time-sorted chat history, tagged by narrative

`gather` no longer produces a long-term list + a separate
cross-narrative blob. It now builds ONE timeline: the current narrative loaded
in FULL (the old 40-cap removed) + cross-narrative via `_load_short_term_memory`,
merged by timestamp, capped at `MERGED_HISTORY_MAX` (30, latest by time). Every
message is tagged `meta_data.narrative_id` (long-term = `ctx_data.narrative_id`;
cross = the source instance's `linked_narrative_ids[0]`) and
`_tag_narrative_aliases()` batch-resolves each id → narrative name for the
`[time · topic · nar_id]` tag the agent sees. `_load_short_term_memory` is now
PURE RECENCY (latest `SHORT_TERM_MAX_MESSAGES`=30 by time; the 2026-05-11
per-instance fairness cap / `SHORT_TERM_PER_INSTANCE` removed — Owner's call).
The reasoning-splice (carry-forward of device codes / job ids / URLs) is KEPT.
Heavy `[ChatHistory]` logging makes assembly verifiable from logs alone. Each
timeline tag now also carries `evt=<event_id>` so the agent can drill into a
turn's full agent-loop/reasoning via view_event; `[ChatHistory] timeline
event_ids` logs the loaded ids for debugging (no raw text). See
[[context_runtime.py]] for rendering + the preamble.

## 2026-05-20 (Fix #2 P2) — recent background-activity track

`_load_recent_actions()` collects the latest `RECENT_ACTIONS_MAX` (10)
`message_type='activity'` rows across the user's chat instances — the centered
small-text items in the UI (job runs, IM/channel activations, bus pings the
agent did WITHOUT replying). These are still filtered OUT of the conversation
timeline; surfaced separately (stored on `ctx_data.extra_data['recent_actions']`)
with each row's `event_id` (for view_event drill-down) and a best-effort job
title pulled from the event's env_context. [[context_runtime.py]] renders them.

## 2026-05-12 P0 #3 followup — drop final_output fallback, defer to step_3 helper_llm

Reverted the 2026-05-11 "use io_data.final_output as reply content"
fallback. That fallback violated the project's thinking-vs-speaking
design (final_output is the agent's internal reasoning, not a
user-facing reply) and could persist meta-talk like "Let me check the
chat history first" as the assistant's spoken line, then poison the
next turn's context. The 5/11 product review with Xiong explicitly
ruled out this shortcut.

The real no-reply recovery now lives one layer up in
`step_3_agent_loop._generate_fallback_reply_stream`: when a chat-
trigger turn ends without `send_message_to_user_directly`, step 3
calls helper_llm with the agent's reasoning as background, streams a
user-facing reply through `AgentTextDelta` (frontend renders it like
any other agent reply), and finally emits a synthetic
`send_message_to_user_directly` ProgressMessage carrying
`details.reply_via="helper_llm_fallback"`.

`after_turn` is now a pure consumer:
- the synthetic ProgressMessage flows through
  `_extract_user_visible_response` like any organic send_message call,
  so `assistant_content` is the helper_llm reply text (not reasoning).
- a tiny scan of `agent_loop_response` lifts
  `details.reply_via="helper_llm_fallback"` onto the persisted row's
  `meta_data.reply_via` field so observability tooling can distinguish
  organic vs. recovered replies.
- if step 3's helper_llm fallback failed too, the row still carries
  `(Agent decided no response needed)` placeholder — that's the honest
  record, not a silent backfill of reasoning.

Pinned by `tests/chat_module/test_error_severity_and_fallback.py`:
- `test_helper_llm_fallback_marker_is_propagated`
- `test_no_reply_tool_and_no_helper_llm_fallback_persists_placeholder`

## 2026-05-11 P0 #3 — error detail, no-reply differentiation, final_output fallback

Three changes addressing the "Agent decided no response needed"
recurring P0 (Lark recviIcuKMNuHj / Xiong's 60% failure rate):

1. **`_detect_error_in_agent_loop` → `_detect_fatal_error_in_agent_loop`**.
   Only `ErrorMessage(severity="fatal")` collapses the turn into a
   failed user-only row. Recoverable signals (mid-loop rate-limit
   blips emitted by ResponseProcessor) keep the turn alive so the
   agent can react and still produce a reply. The old name is kept as
   an alias for backwards compat with existing tests.

2. **Failed-turn rows persist `error_message`, not just `error_type`**.
   `_FAILED_TURN_ANNOTATION_TEMPLATE` now substitutes the actual
   error message into the next-turn annotation, so when the LLM (or
   an operator) reads `[Previous turn failed... Error type: X.
   Detail: Y. Do NOT retry]`, it sees *why* — ops no longer need to
   grep stderr to learn what happened.

3. **`final_output` fallback** (Bug B fix). When
   `_extract_user_visible_response` returns the placeholder but
   `io_data.final_output` is non-empty, we persist `final_output` as
   the assistant content and tag the row with
   `meta_data.reply_via="final_output_fallback"`. Pre-fix, the agent
   would stream LLM-native output to the user (visible mid-turn) but
   the persisted row was just `(Agent decided no response needed)` —
   the next turn's prompt then showed the agent saying it decided not
   to reply, training the model into a self-reinforcing failure loop.
   Production data (RDS, 2026-05-11): chat-trigger placeholders had
   `events.final_output` non-empty in 83/90 cases (92%) — those are
   the rows the fallback will recover.

New `[NO-REPLY]` / `[NO-REPLY-BG]` / `[TURN-FAILED]` / `[FALLBACK]`
WARNING-level log markers fire on each path so ops can grep production
logs and instantly see *which* path a turn ended on and why.

Pinned by `tests/chat_module/test_error_severity_and_fallback.py`.

## 2026-05-11 follow-ups — recency cap + short-term fairness

After landing the per-source dispatch fix, three knobs in this file
got tuned in the same direction (better recall of meaningful history):

- `MAX_RECENT_MESSAGES`: **30 → 40** (chat_module.py around line 432).
  Long narratives were hitting the old cap and silently losing the
  earlier half of the conversation. 40 is still count-based — a
  token-based cap is the right next step if 40 starts to matter.
- `SHORT_TERM_PER_INSTANCE = 5` new constant. `_load_short_term_memory`
  now runs **two stages**: Stage A caps each cross-narrative
  ChatModule instance at its 5 most recent rows; Stage B merges and
  applies the existing `SHORT_TERM_MAX_MESSAGES = 15` global cap.
  Pre-fix, one chatty instance could fill all 15 slots and starve
  every other narrative the user had touched. Pinned by
  `tests/chat_module/test_short_term_fairness.py`.

Both changes are read-side only — no schema, no migration.



`after_turn` used to stamp the injected
`BOOTSTRAP_GREETING` row with `utc_now()` (the moment the hook runs,
i.e. after the agent loop finishes), while the user's first message
carries `event.created_at` (turn-start). Because the agent loop spans
seconds to minutes, the greeting timestamp ended up *later* than the
user message timestamp. Both the chat-history API
(`backend/routes/agents/chat_history.py`, sorts by
`meta_data.timestamp` ascending) and the frontend timeline
(`frontend/src/components/chat/ChatPanel.tsx`, also ascending sort)
then rendered the greeting *under* the user's first query bubble —
the P0 "agent主动问好的消息跑到 query 底下了" filed by Xinyao.

Fix: anchor the greeting at `event.created_at - 1ms` (or
`utc_now() - 1ms` as defensive fallback when `params.event` is None),
keeping the persisted ordering greeting → user → assistant under any
timestamp-ascending sort. Regression pinned in
`tests/chat_module/test_bootstrap_greeting_order.py`.

The frontend never needed changing: the in-session greeting injection
in `ChatPanel.tsx` already stamps `Date.now() - 1`, which dedups
correctly against the (now earlier) DB greeting via the role+content
key inside the 5-minute SAME_MESSAGE_WINDOW.

## 2026-04-28 changes — half-finished features parked

Two writer paths in `after_turn` were exercising
features whose reader half was never built. Cleaned up to stop the
ongoing waste and the noise floor they were creating.

**Part B embeddings (`_embed_message_pair`)** — now actually works.
The `chat_message_embeddings` table is no longer missing from the
schema (it was the only legacy "one create script per table" leftover;
`schema_registry.py` now owns it). Each turn writes one
`(user, assistant)` embedded pair as before, and the writes finally
land. No reader yet — when Part B retrieval is built, it'll find a
populated table to query against. **Cost note:** every turn still
spends one embedding API call on data nobody reads yet. If embeddings
are expensive enough to matter before the reader lands, switch this
back off — but on a per-turn basis the cost is small (~one `get_embedding`
call) so we left it on as future-data investment.

**ChatModule status report (`update_report_memory`)** — disabled.
The block that built a one-line "Conversation rounds: N | Latest …"
report and called `event_memory_module.update_report_memory(...)` is
commented out (in place, with explanation) inside
`after_turn`. Two reasons stacked:
  1. The reader half (`get_report_memory`) has zero callers anywhere
     — no Narrative orchestration code consumes the reports.
  2. The writer was failing in production anyway because the live
     `module_report_memory` table still has a legacy
     `instance_id NOT NULL` column from an older schema, and the
     current INSERT (narrative_id / module_name / report_memory) does
     not fill it. After T12's `error → exception` sweep the failure
     started printing a full SQLite stack to logs every turn, which
     is what surfaced the bug.

The block stays as commented code (not deleted) so reviving the
feature is a one-block-toggle once a `get_report_memory` consumer
lands. Don't uncomment without first reconciling the
`module_report_memory` schema — see
`.mindflow/mirror/.../event_memory_module.py.md`.

## 2026-04-23 update — 持久化 Agent reasoning 以跨 turn

`after_turn` 现在除了保存 `send_message_to_user_directly` 的 content（用户可见文字），还把 `params.io_data.final_output`（Agent 的 reasoning）**完整**存到 assistant 消息的 `meta_data.reasoning`。曾考虑过加长度 cap，决定**不截断**——reasoning 是 Agent 自己写的（自然自限长），而且截断会冒风险切掉正是 Agent 要跨轮保留的那个长串（device_code、file token）。

`gather` 在所有 load + sort 完成后，遍历 `all_messages`：对每条 assistant 消息，如果 `meta_data.reasoning` 非空，把 content 包成：
```
<my_reasoning>
{reasoning}
</my_reasoning>

<reply_to_user>
{original content}
</reply_to_user>
```

**动机**（2026-04-23 产线事件，agent_7f357515e25a）：增量 Lark scope 授权时，`auth login --no-wait` 返回的 `device_code` 值只在那一轮的 `tool_call_output_item` 里，不跨 turn。Agent 下一轮想用 `--device-code <D>` poll 时拿不到 `D`，只能写出 `auth login --device-code --as ...`（缺值），回退到 `--no-wait` 重铸——orphan 用户点过的 URL。本修改让 Agent 可以通过在 reasoning 里 restate 关键值（device_code、job_id、token 等）把它们带到下一轮。前端 chat_history API 拿到的 row 不变（content 字段还是 send_message 原文），splicing 只发生在**喂 LLM 之前的那一次渲染**；持久化的 row 只是多了 `meta_data.reasoning` 字段。

配套变更：
- `plugins/builtin.basic_info/src/narranexus_plugins/basic_info_module/prompts.py` 新增 "Working Memory Across Turns" 段，向所有 trigger 源的 Agent 说明"tool output 一次性，reasoning 跨轮"这件事 + 要求 Agent 主动 restate 关键值到 reasoning
- `plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/lark_module.py::_INCREMENTAL_AUTH_GUIDE` 追加一条 bullet，明确说 mint 完后要把 device_code/scope/URL 写进 reasoning
- 回归 pin 在 `tests/chat_module/test_reasoning_persistence.py`（持久化 + splicing 双向）、`tests/basic_info_module/test_cross_turn_memory_guidance.py`（prompt 三句话）、`tests/lark_module/test_incremental_auth_guide.py::test_guide_reminds_agent_to_restate_device_code_in_reasoning`

**不改前端** — frontend 的 chat bubble 照旧读 `get_simple_chat_history` 返回的 content，看到的还是 send_message 原文。meta_data.reasoning 仅供后端组装 LLM 上下文用。

# chat_module.py — ChatModule 实现

## 为什么存在

ChatModule 解决两个核心问题：让 Agent 在对话中访问过去的交流历史，以及在对话结束后把这轮对话持久化。它同时定义了"用户可见响应"的提取逻辑——只有通过 `reply_owner` 工具发送的内容才算用户可见，Agent 的内部推理过程不记录为 assistant 消息。

**Hook 实现**：同时实现了 `gather`（双轨记忆加载）和 `after_turn`（对话持久化）。

**MCP 端口**：7804

**Instance 模型**：Narrative 级别，每个 Narrative 里每个用户有独立的 Chat 实例（`instance_id` 格式：`chat_xxxxxxxx`）。

## 上下游关系

- **被谁用**：`ModuleLoader` 自动加载；`HookManager` 调用两个 hook；`ModuleRunner` 启动 MCP
- **依赖谁**：`EventMemoryModule`（存储后端）；`InstanceRepository`（短期记忆时查找其他 Chat 实例）；`_chat_mcp_tools.py`（MCP 工具实际定义）；`bootstrap/template.BOOTSTRAP_GREETING`（首次对话时注入问候语）

## 设计决策

**双轨记忆的优先级**：EverMemOS 语义记忆（`ctx_data.extra_data["evermemos_memories"]`）优先于 DB 事件记忆。如果 EverMemOS 没有数据（新 Narrative、EverMemOS 不可用），则 fallback 到直接从 `EventMemoryModule` 读取历史。EverMemOS 路径不依赖 `EventMemoryModule`，是更高质量的语义压缩记忆。

**短期记忆移除了时间窗口限制**（2026-02-09 优化）：早期版本限制 30 分钟内的跨 Narrative 消息，但这导致非活跃用户的短期记忆总是空。改为直接取最近 15 条（`SHORT_TERM_MAX_MESSAGES = 15`），不论时间。

**背景任务的 activity record 而非 fake 对话**：当 `working_source != "chat"` 且 Agent 没有调用 `reply_owner` 时，不记录一对 user/assistant 消息，而是记录一条 `message_type: "activity"` 的简短描述（如 "Executed a background job"）。防止历史记录被无意义的 "(Agent decided no response needed)" 污染。

**失败轮隔离（Bug 8）**：当 agent loop 抛错时，`_detect_error_in_agent_loop` 从 `params.agent_loop_response` 扫出 `ErrorMessage`（`step_3_agent_loop.py` 在 catch Exception 分支里把 ErrorMessage 既 yield 也 append，保证下游 hook 看得到），`after_turn` 只存 user 消息，`meta_data` 里打 `status="failed"` + `error_type=...`，**不写任何 assistant 行**（partial 输出也丢）。下一轮 `gather` + `_load_short_term_memory` 都会过 `_apply_failed_turn_filter`：失败的 user 行被重写成"Previous turn failed... Do NOT retry"的注解（保留原问题文本，方便代词解析），遗留的失败 assistant 行被丢。目的是让 LLM 看到"那轮断了"而不是"那轮我只说了一半还没说完"——后者正是污染下轮 prompt 让 LLM 重复执行上轮查询的根因。

**MCP 工具逻辑抽取到 `_chat_mcp_tools.py`**：2026-03-06 拆分，保持 `chat_module.py` 专注于 Hook 生命周期，MCP 工具注册逻辑独立维护。

## Gotcha / 边界情况

- **Bootstrap greeting 注入**：如果 `ctx_data.bootstrap_active=True` 且是第一轮对话（历史为空），会在写入历史前先插入一条问候语作为第一条 assistant 消息。这是一次性逻辑，仅发生在 Agent 第一次被激活时。问候语经 `_resolve_bootstrap_greeting()` 解析：优先读 `agents.agent_metadata.bootstrap_greeting`（场景化 provisioner 写入，如 Arena onboarding），缺失时退回通用 `BOOTSTRAP_GREETING` 常量——通用常量保持场景无关（铁律 #4）。
- **`channel_tag` 的传递**：`after_turn` 里从 `ctx_data.extra_data["channel_tag"]` 读取渠道信息（Matrix 房间、发送者等）并写入每条消息的 `meta_data`。如果 `channel_tag` 是 Pydantic 对象（而非 dict），会调用 `.to_dict()` 转换。忘记这个转换会导致 JSON 序列化失败。

## 新人易踩的坑

- 误以为 `instance_id` 就是用户 ID——`chat_xxxxxxxx` 是 Module 实例的 ID，不是用户 ID。一个用户在不同 Narrative 里有不同的 Chat 实例。`get_chat_history` 工具需要的是 `instance_id`，不是 `user_id`。
- 调试时看到 `chat_history` 为空但数据库里有记录——通常是 `instance_id` 不对导致的：ModuleLoader 注入的 `instance_ids` 不包含要查的实例。

## 2026-08-13 — `_origin_delivered_text`:投递过的轮次要记下**说了什么**

上一批改动让 team turn 的投递被正确判定为"已投递",然后**声称**行类型会随之变成真
assistant 行。**那是错的。** 分支选择器读的是 `is_no_response`,而它来自
**owner-visible** 抽取 —— `extract_owner_visible_text` 在读 `PLATFORM_REPLY_TEXT_KEY`
**之前**就先过 `is_owner_visible_reply_tool`,对 `bus_send_message` 返回 None。于是
`assistant_content` 被兜底成 "(Agent decided no response needed)"、`is_no_response`
为真、落 `activity` 行、两个加载器照旧丢掉。`delivered_to_origin` 当时的全部作用只是
换了摘要文案加一个 meta 字段。

**owner-visible 那道门必须继续说 None** —— 它正是拦住"每次团队回复重新锚定 owner
会话"的东西(PR #230)。所以不能靠提升 `bus_send_message` 来解决。

真正的问题是把"owner 没看见"读成了"什么都没说"。`_origin_delivered_text` 用
**origin** 抽取(全量 `user_reply_tool_names`)取回真正说出去的文本,`is_no_response`
为真但取到文本时就用它当 content 并翻转结论。「owner 看见了吗」和「这是 agent 真说过
的话吗」是两个问题,只有后者决定它下一轮记不记得。

真沉默的轮次一字未动:仍写 activity 行、仍被过滤器丢掉,
`test_filtered_activity_row_invisible_to_long_term` 仍绿。这是治误分类,不是放宽过滤器。

## 2026-08-13 (review 后) — 一个判定一份实现,一条死分支清掉

`_delivered_to_origin` 与 `_origin_delivered_text` 是同一段抽取的两份逐行拷贝。现在
前者实现为 `bool(后者)` —— 它们**一致**这件事正是行类型逻辑成立的前提,而两份实现里
任何一边改了抽取规则(比如将来跳过某类 tool_name),另一边不会跟着变,后果是行被静默
归错档。

**activity 分支里的 `delivered_to_origin` 恒为 False,分支已清掉。** 能让它为真的输入
必然让 `_origin_delivered_text` 非空,于是上面那段恢复会把 `is_no_response` 翻成 False、
走 assistant 分支 —— 根本到不了这里。`[DELIVERED-BG]` 那条日志因此永远不会打印,而
**一条永远不打印的日志比没有更糟**:按它做看板的人会得出"bus turn 从不投递"的结论。

`not turn_interrupted` 那个守卫也是冗余的:被打断的一轮兜底文案是
"(Interrupted by user)",不是 no-response 标记,两个条件互斥。留着会让下一个人去找
一个不存在的情形。

## 2026-08-14 — 删掉零调用方的 `_delivered_to_origin` 与不可达的摘要分支

文本恢复那块落地之后,`_delivered_to_origin` 在生产**没有任何调用方**,
`_build_activity_summary` 的 `delivered_to_origin` 形参与它的 `"Replied to X"` 分支也
永远走不到(能让它为真的输入必然让 `_origin_delivered_text` 非空,于是那一轮走的是
assistant 行)。两者一并删除,测试改指仍然活着的 `_origin_delivered_text`。

留着的代价不是运行时错误,是**代码对自己撒谎**:下一个人读到会以为 activity 行仍在
做投递分类。

## 2026-08-18 — `disallowed_tools(ctx_data)` 签名同步

跟随 [[base.py]] 2026-08-18 的接缝修复：压制 hook 改读本轮自己的 ctx，不再依赖声明 hook
留下的实例状态（`_last_ctx` 已删）。收集环先压制后声明，旧写法在全新实例上必然误判。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

## 2026-09-04 · declares its platform metadata (batch 5b)

`get_config()` is static and carries what the platform used to table about this module (default / base / always-load membership, instance prefix, role, display, decision metadata as applicable).
