---
code_file: src/narranexus/platform/channel/channel_audit_events.py
stub: false
last_verified: 2026-08-24
---

## 2026-08-24 — ingress 熔断器三个事件

`EVENT_INGRESS_BREAKER_TRIPPED` / `_CLEARED` / `EVENT_INGRESS_DROPPED_BREAKER`。
沿用本文件既有命名法，值是同名的小写下划线形式。

三个事件合起来让 [[ingress_guard.py]] 的一生完全可以只从 DB 读懂：**什么时候
关上了门、每一条撞在关着的门上的消息、什么时候又开了**。

`tripped` 同时覆盖首次跳闸和每一次升级，由 `details.transition` 区分——
与 subscriber 熔断器「一个事件类型 + details 变化」的处理一致。

`ingress_dropped_breaker` **逐条写**是刻意的：「机器人怎么六小时不说话了」
必须能回答，而静默 return 正是让原事故跑了 70 小时无人察觉的那类盲区
（事故教训 #3/#5）。

## 2026-08-10 — managed 面 + manyfold files 写入的事件常量

`managed_ingress_denied` / `managed_ingress_silent` /
`managed_attachments_converted` / `manyfold_files_write`。动机 = 教训
#5:托管消息"没结果"必须和原生一样可从 DB 回答;`managed_ingress_processed`
(run 完结行)早于本批,由 `managed_after_run` 写。files_write 行的
channel 列固定 "manyfold"(它不是渠道,是平台 ingest 腿)。

## 2026-08-06 — ingress_dropped_empty

新增 `EVENT_INGRESS_DROPPED_EMPTY`：`_process_message` 的空内容守卫
（无正文且无 attachment_refs → 丢弃）此前是裸 `return`，与
`ingress_dropped_unparsed` 同属审计盲区。8/6 事故：无语言包裹的 post
payload 被提取成空串后从这里消失，零痕迹。base 与 Lark 覆写两处守卫
现在都写这条审计。

## 2026-08-04 — subscriber isolation events

Three constants for the two gates in [[channel_trigger_base.py]]:
`EVENT_SUBSCRIBER_BREAKER_TRIPPED` / `EVENT_SUBSCRIBER_BREAKER_CLEARED`
(fast-death breaker) and `EVENT_SUBSCRIBER_UNSTARTABLE` (the
`should_start_subscriber` pre-flight). One row per state change — trip
carries consecutive_fast_deaths / isolated_seconds / trip_number, clear
carries a `reason` (`credential_changed` / `backoff_expired`) so even a
re-probe is visible rather than showing up as an unexplained gap between
two trips. Together they are the DB-side answer to "why is this agent's
subscriber not running?", which the unbounded death/rebirth WARNING spam
never gave (lesson #5).

## 2026-07-29 — EVENT_INGRESS_DROPPED_NOT_MENTIONED

New ingress-drop reason for group-room messages that did not @-mention
the bot (first consumer: [[lark_trigger]]). Replying to every group
message is the most visible way a channel bot can misbehave, so the drop
is deliberate — but a silent drop would make "the bot ignored me in the
group" unanswerable, which is exactly the class of unanswerable ticket
`EVENT_INGRESS_DROPPED_UNPARSED` was added for. Same reasoning, same
shape.
## 2026-07-03 — `EVENT_INGRESS_DROPPED_UNPARSED`

New ingress constant for raw events rejected by parse_event (unsupported
message types). Emitted by ChannelTriggerBase._on_unparsed.

## 2026-07-02 — `EVENT_TRANSPORT_SEND_FAILED` (Matrix Commit 4b)

New shared audit type for reply-side transport failures. Added when
MatrixTrigger's `_send_matrix_reply` exhausts retries or hits a
permanent auth error on `client.room_send`. Kept generic so any
channel whose reply path can fail out-of-band after the agent
finished can emit it — Slack / Telegram might too once their reply
tools land on this pattern. Distinct from `inbox_write_failed`:
that one is our own DB row; this one is the platform refusing our
outbound message. Details carry `error_code` (M_LIMIT_EXCEEDED /
M_UNKNOWN_TOKEN / transport_exception / no_active_client / …),
`attempts`, and a truncated `body_preview` for post-mortem.

## Why it exists

Single source of truth for the event-type strings that flow into
``channel_trigger_audit.event_type``. Module-level string constants
(NOT an enum) so callers can grep for them and the DB column stays a
plain VARCHAR — adding a new event type does not require a schema
migration.

## Design decisions

- **``transport_*`` instead of ``ws_*``**. The Lark trigger's audit
  uses ``ws_connected`` etc. because Lark is exclusively WebSocket.
  Telegram long-polls and Slack offers Socket Mode + Event API, so
  the abstraction layer uses the more general ``transport_*`` prefix.
  Phase 2 will redirect Lark writes here too — until then the two
  vocabularies coexist.
- **``EVENT_DEBOUNCE_MERGED`` is new** — Lark today has no debounce.
  Phase 1 ships the merger and the audit type together so post-incident
  reviewers can correlate "user sent 3 in a row" with "agent ran once".
- **Phase 1a attachment-ingestion trio**:
  ``EVENT_INGRESS_DROPPED_OVERSIZED``,
  ``EVENT_ATTACHMENT_FETCH_FAILED``, ``EVENT_ATTACHMENT_PERSISTED``.
  Emitted by ``ChannelTriggerBase`` + per-channel
  ``fetch_attachments``. Kept distinct so ops can tell platform-cap
  refusals apart from network failures apart from happy-path persists.
  All three strings are ≤ 32 chars to keep the ``event_type`` index
  lean (see ``tests/channel/test_audit_events_attachment.py``).

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase`` (writes most events);
  ``ChannelTriggerAuditRepository`` (re-exports for caller convenience).
- **Downstream**: any /healthz endpoint or admin UI that surfaces
  audit data.
