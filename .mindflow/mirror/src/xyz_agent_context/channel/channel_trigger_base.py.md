---
code_file: src/xyz_agent_context/channel/channel_trigger_base.py
stub: false
last_verified: 2026-08-04
---

## 2026-08-04 — 起不来的凭据：预检门 + 快速死亡熔断器

prod 实锤（8/1-8/3）：某 agent 的 Lark App Secret 被清空后，订阅器每次启动
即静默 `return`（不抛异常，`is_permanent_auth_failure` 永远看不见），
watcher 无条件重启同 key → ~28s 死亡重生死循环，4h 刷 1498 次 WARNING。

**两道闸，先便宜后通用**：

1. `should_start_subscriber(cred)` 覆写钩子（默认 True，不做 I/O）——能只
   看凭据就断定「连都连不上」的，一次都不启动。Lark 覆写为
   `receive_enabled()`（secret 空即 False），本次事故因此是 0 重启 0 ERROR。
   拒绝走 `_note_unstartable`：**按指纹去重**，一个凭据状态只报一次
   （WARNING + `subscriber_unstartable` 审计行），换绑后重报。
2. 熔断器——一切**事前不可知**的兜底。每 key 数连续快速死亡（存活 <
   `BREAKER_FAST_DEATH_SECONDS`=60s），连续 `BREAKER_FAST_DEATH_THRESHOLD`
   （3）次触发，按 `BREAKER_BACKOFF_SCHEDULE_SECONDS`（5min→30min→2h，
   末档循环）隔离重探。与 `is_permanent_auth_failure` 互补而非替代：那条
   路需要可识别的异常类，熔断器认的是「反复秒死」这个形状。

**指纹是本设计最锋利的一处，两次都能把熔断器废掉**（review 抓出，8/4 修）：

- **取样时机**：指纹必须取自「这次启动实际用的凭据」
  （`_subscriber_start_fingerprint`，启动时落），不能取 `_subscriber_creds`
  ——后者比本轮 poll 慢一拍，两次 poll 之间任何一次写库都会被读成换绑，
  熔断刚跳闸就在同一轮被清除。
- **字段范围**：必须排掉订阅器自己回写的簿记字段
  （`BREAKER_VOLATILE_CREDENTIAL_FIELDS`，子类扩展）。Matrix 的
  `matrix_since_token` 每次 sync 都写、Lark 连上后写 `bot_open_id`/
  `bot_name`——不排掉就是「自己的流量把自己的熔断器清了」。
- 兜底：指纹触发的解除走 `_breaker_release`（**保留** `_breaker_trips`
  升档记忆），所以哪天漏排一个高频字段，代价是每档 3 次重启并继续升档，
  而不是无限风暴。watcher 会在订阅器**仍存活**且越过 60s 快死窗口时立即
  `_breaker_forget` 全清；判定时还必须检查 task 尚未结束，防止同轮 reap 的
  `await` 期间产生竞态。不能要求健康订阅器先死亡才结算，否则一次成功的退避
  恢复会永久抬高下次故障的隔离档位。
- 默认实现仍以字段 `repr` 组成指纹，因此 auth-relevant 字段值必须跨 DB load
  有稳定 repr；地址型对象字段必须列入 volatile，或由子类覆写成稳定编码。
  顶层凭据对象不可 introspect 时使用常量指纹，fail-safe 保持隔离。

其余不变量：健康存活（≥60s）清零 streak + trips（网络抖动不误熔断）；主动
`_stop_subscriber` 先 pop 掉启动标记（不计死）；凭据整行消失
`_breaker_purge_stale` 清全部五个 dict；`stop()` 一并清空（生命周期对称）。
日志/审计：trip=ERROR 一条 + `subscriber_breaker_tripped`；每次解除（含
退避到期重探）=INFO 一条 + `subscriber_breaker_cleared` 带 `reason`，
DB 轨迹不留空白（教训 #5）。心跳与 `/healthz` 另外常驻上报隔离态
（教训 #4，见 [[channel_health_server.py]]）。
Pin: tests/channel/test_credential_breaker.py。

## 2026-08-04 — 第三个 managed 缝:`managed_silent_ingest`(review)

静默摄取从协调器收回本类:批量调用的形状(credential/sender 表/
attachments_by_index)是 trigger 的私有编排知识,协调器伸手进私有方法
= 缝要消灭的耦合;渠道也因此获得覆写点(某渠道可选择不做静默)。
## 2026-08-03(补) — `_persist_attachment` 尾段抽到共享函数

store+STT+Attachment 构造迁至
`attachment_storage.persist_attachment_bytes`(managed ingress 转换器
共用);trigger 保留 owner 解析 + MIME sniff。行为不变。

## 2026-08-03 — managed-ingress 缝(start() 之外复用业务钩子)

新增 `_managed_bind` / `_credential_for_agent` / `managed_before_run`
/ `managed_after_run`:Manyfold 托管模式下平台持有连接与清洗,
openai_compat 经 managed_channel_ingress 构造 trigger(不 start)并在
run 前后调这两个缝。默认 before=放行;after=错误兜底(run 失败且无回复
→ `_send_error_fallback` + `format_error_reply(RunError)`)+ 原生
inbox write(无回复写 CHANNEL_SILENT_SENTINEL,与原生 extract_output
语义一致)+ `managed_ingress_processed` 审计行(教训 #5)。
`_managed_bind` 等价于 start() 里业务钩子所需的最小状态(_db +
audit repo),幂等。覆写:wechat(认主)、matrix(authorize)。

## 2026-07-31 — _resolve_agent_owner 委托 AgentRepository.resolve_owner

行为不变（miss/异常仍回 ''），实现收敛到 repository seam——属主语义
从此一个家（PR #219 review 收敛三份拷贝）。

## 2026-07-22 — _sniff_mime delegates to the shared utils helper

``_sniff_mime`` now calls [[mime_sniff]] (one tiering for IM downloads, WS
uploads and team-chat uploads). Two deltas for THIS path: the platform
``hint`` dropped from tier 2 to last resort (extension guess now outranks
it; content sniff still outranks both), and the audio/video container
override applies here too — an audio-only WebM/Ogg/MP4 whose platform hint
says ``audio/`` no longer classifies as ``video/``.

## 2026-07-10 — "ack early" moved into the per-turn input (salience)

The early-feedback directive used to live in each module's `get_instructions`
(system prompt) where models deprioritized it. It now rides in the **per-turn
input** instead: `_build_and_run_agent` prepends `_early_feedback_prefix(message)`
to `tagged_prompt` (right after the channel tag), so the "ACK FIRST" line sits
inline with THIS message — higher salience. `_early_feedback_prefix` uses the
new `react_tool_ref` class attr (bare name / Lark's `mcp__…` / None for WeChat →
message-only ack) + the real room/message ids, via `render_early_feedback`.
Still a SHOULD (prompt, not a hard guarantee). Lark's `_build_and_run_agent`
override injects the same prefix.

## 2026-07-10 — surface source_message_id (agent-driven feedback enabler)

`_build_and_run_agent` adds `source_message_id` (the inbound platform message
id) to `trigger_extra_data`. It merges into `ctx_data.extra_data`, so a channel
module's get_instructions can tell the agent which message to react to /
reply in-thread (the agent-facing `react_to_user_message` tool). Kept here, not
in ChannelTag, so it stays ephemeral (not persisted into chat-history tags).

## 2026-07-08 — `pre_start(db)` hook added for the consolidated supervisor

New optional lifecycle hook `pre_start(db)` (default no-op), called by the
consolidated supervisor (`module/run_channel_triggers.py`) BEFORE `start(db)`.
Subclasses override it to run their own idempotent one-off migration inside the
channel instead of in the shared entrypoint (rule #4). First user:
`LarkTrigger.pre_start` carries the legacy `auth_status` migration that used to
live in the now-deleted `run_lark_trigger` entrypoint. The design decision
"6 abstract methods + 1 optional hook + 2 PUSH stubs" below now reads "…+ 2
optional hooks…" (`fetch_attachments` and `pre_start`). Consolidation relies on
`start()` already being non-blocking + all state being per-instance, so N
triggers coexist in one event loop.

## 2026-07-07 — error-fallback: surface run failures INTO the channel

Problem: IM delivery is "agent calls its own reply tool during the run; the
trigger only scrapes the sent text for the inbox." So if a run FAILED before
the agent reached its reply tool, nothing was sent to the channel — the user
saw silence, indistinguishable from the agent choosing not to answer. Chat had
a helper_llm fallback; IM was excluded. (slack/discord/telegram/wechat wrote
the error to the inbox ONLY; lark already sent it; matrix uses streaming
markers.)

Fix — three pieces, all in `_build_and_run_agent`:
1. New overridable hook `send_channel_reply(credential, message, text)` —
   default no-op; each IM subclass implements it with the per-subscriber SDK
   client it already holds, addressing via `message.chat_id` / `sender_id` /
   `raw`. The runtime CANNOT do this itself (it has no channel client — those
   live on the trigger), which is why the fallback lives at the trigger layer.
2. `_send_error_fallback(...)` sends the error via the hook UNLESS the agent
   already replied this turn (`already_replied` → don't double-message),
   best-effort (a send failure is logged, never masks the original error).
3. `_build_and_run_agent` now: wraps `run_and_collect` in try/except (a hard
   raise, not just a yielded ERROR, still notifies — no silent crash); on
   `result.is_error` computes `already_replied` from `extract_output` vs
   `CHANNEL_SILENT_SENTINEL` and fires the fallback.

**Key safety property**: the fallback fires ONLY on `is_error` (or a raise). A
run that stays silent by CHOICE never sets `is_error`, so intended silence
(group non-@, nothing to add — see `_build_and_run_agent_silent_batch`) is
never disturbed. This deliberately does NOT recover the "agent wrote a reply
but forgot to call the send tool" (`no_reply`) case for IM — too ambiguous vs
intended silence; only errors are surfaced.

`CHANNEL_SILENT_SENTINEL = "(stayed silent)"` is now a shared module constant
(was hard-coded identically in 5 channels) so the base can tell "agent stayed
silent" from "agent replied" when gating the fallback. Lark's bespoke error
send was consolidated onto the same hook.

## 2026-07-02 — `_build_and_run_agent_silent_batch` for group non-@ ingestion

New instance method on the base: takes a non-empty list of
`ParsedMessage` (same chat_id, chronological), merges into one
`input_content` line-per-message with `[ts] Display: body`, then calls
`get_agent_runtime_client().run_and_collect(..., silent=True,
trigger_extra_data={"batch_messages": [...]})`. Per-message metadata
(event_id / timestamp / sender_id / sender_name / attachments) rides
in `batch_messages`; ChatModule's silent-batch write path (see
[[chat_module.py]]) reads it and appends N user rows to
`instance_json_format_memory` with NO assistant row.

Why here (channel-agnostic): all IM triggers have the same "group
message that didn't @ us" shape — Slack currently drops these at the
event boundary, Lark runs the full agent on every message, both
suboptimal. Landing the silent-batch shape on the base means each
trigger only needs a classification step (dm / group_mention /
group_silent) + a debounced flush; the batch runtime plumbing is
shared. Matrix (Commit 4b) is the first consumer.

The method is fire-and-forget for output: silent runs produce no
user-facing text, so no return value; failures inside the runtime
call are logged and swallowed to keep the sync loop / debounce timer
advancing (a dropped batch is recoverable via reconnect + since_token
replay, a crashed trigger is not).

## 2026-07-03 — CONTENT_DEDUP_WINDOW_SECONDS + _content_fingerprint (X1)

New opt-in class attr (default 0) wires ChannelDedupStore's
content-fingerprint layer; `_content_fingerprint` hashes
(chat_id|sender_id|content) → sha256[:32]. Policy stays in the subclass
(NarramessengerTrigger sets 20 min to cover the platform's 15-min
re-dispatch deadline); base computes the fingerprint so every channel
shares one identity definition. Chosen over shrinking
PROCESS_MESSAGE_TIMEOUT below the platform deadline, which would cut slow
LLM turns short (铁律 #14).

## 2026-07-03 — unparsed raw events now audited (`_on_unparsed`)

`parse_event(raw) -> None` (stickers/images/voice on text-only channels)
used to hit a bare `continue` — no log, no audit row, unanswerable "why
didn't the bot reply?" tickets (lessons #3/#5; 2026-07-03 wechat incident
burned an hour proving a message was never parseable). The subscriber loop
now calls `_on_unparsed`, which writes `ingress_dropped_unparsed` with the
raw item's KEYS only (never payloads — media bytes / text stay out of the
audit table).

> Concrete subclasses today: ``LarkTrigger``, ``SlackTrigger``,
> ``TelegramTrigger``, ``DiscordTrigger``. (The file docstring's old
> "Lark is NOT a subclass" line was stale; corrected when Discord landed
> — all four are subclasses now.)

## Why it exists

Phase 1's centerpiece. Direct extraction of the channel-agnostic 80%
of ``LarkTrigger`` into a base class so Slack (Phase 3) and Telegram
(Phase 4) ship without re-implementing dedup, Worker Pool, audit log,
inbox writer, or credential watcher. Lark itself stays put in Phase 1
— Phase 2 will refactor it onto this base.

This is the locus of the architecture's "Pattern C: shared subscriber"
principle (see ``.mindflow/project/references/architecture.md``).

## Design decisions

- **6 abstract methods + 1 optional hook + 2 PUSH stubs.** Subclasses
  implement ``connect``, ``parse_event``, ``is_echo``,
  ``resolve_sender_name``, ``create_context_builder``,
  ``load_active_credentials``. The optional ``fetch_attachments`` hook
  (added in Phase 1a, default returns ``[]``) lets channels with media
  ingestion override without forcing text-only channels to do anything.
  PUSH-mode stubs (``handle_webhook``, ``verify_webhook``) raise
  ``NotImplementedError`` until Phase 6.
- **``_persist_attachment`` helper.** Lives in the base because the
  download → MIME sniff → on-disk store → optional STT path is fully
  channel-agnostic. Each channel subclass downloads bytes from its own
  SDK then hands them to this helper. Workspace path resolution mirrors
  WS upload exactly (``_resolve_agent_owner(agent_id) or agent_id``)
  so the agent's Read tool finds the file at the same path the
  attachment was written to.
- **Attachment list flows via ``trigger_extra_data["attachments"]``.**
  Mirrors ``backend/routes/websocket.py:644-648`` so ChatModule's
  ``hook_data_gathering`` (which reads
  ``ctx_data.extra_data["attachments"]``) treats IM-uploaded and
  WS-uploaded files identically. The base only sets the key when the
  list is non-empty — keeps text-only audits noise-free.

- **Caption-less file uploads MUST flow** (Phase 1b regression-fix).
  The empty-content guard in ``_process_message``:
  ``if not message.content.strip(): return`` was originally written
  in Phase 1a when ParsedMessage was text-only — an empty content
  was a clear no-op. Phase 1b made files first-class, but the guard
  wasn't updated. Real-world failure mode: user drag-drops a PDF
  into Slack DM without typing anything → ``text=""`` +
  ``files=[...]``. parse_event correctly extracted ``attachment_refs``
  into ``raw``, but the base guard cut the message off BEFORE
  ``fetch_attachments`` could ever run, with NO audit row at all
  (the audit trail just stopped at ``debounce_merged``). The guard
  now keeps the early-return only when BOTH ``content`` is empty
  AND ``raw["attachment_refs"]`` is empty. Pin tested by
  ``tests/channel/test_attachment_fetch_pipeline.py::test_caption_less_file_upload_still_processed``.
- **Lazy AgentRuntime import.** Eager top-level import causes a
  circular load: ``channel/__init__.py`` re-exports
  ``ChannelTriggerBase`` for ergonomic use, but
  ``module/__init__.py`` (loading LarkModule) reaches the channel
  package first, so importing AgentRuntime here would re-enter the
  partially-initialised module package. Lazy-loading inside
  ``_build_and_run_agent`` breaks the cycle without forcing callers
  to import from the longer ``channel.channel_trigger_base`` path.
- **Tunable class attributes, not constructor args.** ``MIN_WORKERS``,
  ``MAX_WORKERS``, ``PROCESS_MESSAGE_TIMEOUT_SECONDS`` etc. are class
  attributes so subclasses can override them in 1 line; constructor
  args reserved for instance state (``base_workers``,
  ``history_config``).
- **``_subscriber_key`` override hook.** Defaults to
  ``credential.app_id`` (Lark today). Slack workspace install can
  serve multiple agents per ``team_id``; that channel will override
  to a compound key without changing the base.
- **Owner resolution via ``agents.created_by``.** AgentRuntime needs
  the agent's OWNER user_id to map provider quotas, NEVER the IM
  sender_id. This bug bit Lark previously and is fixed once here.
- **Per-message timeout, not just stream timeout.** The 30-min
  ``PROCESS_MESSAGE_TIMEOUT_SECONDS`` cap exists because
  ``collect_run`` only times out on stream silence, not total
  wall-clock — without this a stuck LLM call could occupy a worker
  forever.
- **``_prune_dead_workers`` between sizing decisions.** A worker that
  silently dies (cancellation leak, async-for oddity) would otherwise
  keep ``_adjust_workers`` from spawning a replacement, leaving the
  queue to grow unbounded. Lark's H-4 fix preserved here.
- **Credential cache is refreshed every poll, not just on subscriber
  start.** The credential is a DB snapshot whose ``permission_state`` /
  ``auth_status`` change mid-session — most importantly when the owner
  completes the three-click user authorization, which is what flips
  ``LarkCredential.user_oauth_ok()`` and lets ``resolve_sender_name``
  read names via the user token. The transport (``connect``) captures
  its credential once and the long-lived stream never re-reads it, so
  ``_credential_watcher`` overwrites ``_subscriber_creds[key]`` with the
  fresh DB snapshot on every poll, and ``_worker`` re-resolves the
  credential from that cache at dequeue time. Net effect: a mid-session
  auth completion (or any credential change) takes effect on the next
  message without restarting the subscriber or dropping the connection.
  Found on EC2: every Lark sender stayed "Unknown" because the running
  subscriber was started before the owner finished authorizing.

## Upstream / downstream

- **Upstream**: subclasses (Phase 2 will rebase ``LarkTrigger``;
  Phase 3 adds ``SlackTrigger``; Phase 4 adds ``TelegramTrigger``).
- **Downstream**:
  - ``ChannelDedupStore`` (3-layer dedup)
  - ``ChannelDebounceMerger`` (optional)
  - ``ChannelInboxWriter`` (5-row bundle)
  - ``ChannelTriggerAuditRepository`` (audit log)
  - ``ChannelSeenMessageRepository`` (durable dedup, owned by the
    dedup store)
  - ``AgentRuntime`` + ``collect_run`` (lazy import)
  - ``ChannelTag`` (prompt injection)

## Gotchas

- ``working_source`` defaults to ``WorkingSource.CHAT`` because every
  enum value must exist before the subclass picks one — Phase 1
  doesn't add ``WorkingSource.SLACK`` etc. Subclasses MUST set this
  to a meaningful value before ``start()``.
- ``handle_webhook`` raises ``NotImplementedError`` with a Phase-6
  reference — do NOT remove the references; webhooks need both
  HTTP routing AND a per-channel parser, neither of which is built.
- ``stop()`` flushes any debounce-buffered messages then drops them
  with a log line. We don't try to enqueue them on shutdown because
  the credential context (set per-flush in
  ``_enqueue_or_debounce``'s closure) isn't reachable from
  ``flush_all``. If a future channel really needs strict shutdown
  drain semantics, encode the credential into ParsedMessage.raw and
  reconstruct in ``_enqueue_debounced``.
