---
code_file: frontend/src/lib/buildTimeline.ts
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — no-response marker comes from turnMarkers

The non-chat junk filter compares `msg.content === NO_RESPONSE_MARKER`
(imported from [[turnMarkers]]) instead of repeating the literal. Still an
exact, language-independent comparison: the marker is the persisted
English string, localized only by `MessageBubble` at render time.

## 2026-09-08（dev 实测）— 折叠问候按身份去重

`BOOTSTRAP_GREETING_SESSION_ID` 的会话消息在历史含 `bootstrap` 行时直接丢弃；此前靠内容匹配，前端中文副本对不上后端英文原文，回合结束后问候显示两遍、刷新才消失。

## 2026-08-21 — 不再在 history 循环里丢弃 message_bus 活动行

删掉了 history 循环里 `message_type === 'activity' && working_source === 'message_bus'`
的 `continue`。它原意是「别让 message_bus 活动出现在 1:1 对话里」,但生产里
**所有** peer/team turn 的 `working_source` 都是 `message_bus`(`MessageBusTrigger._invoke_runtime`
对 A2A/team/patrol 一律写 MESSAGE_BUS,全仓无代码写 `a2a`)——于是这条 filter 把 Activity Log
要显示的行**全部**丢掉,bug 2 端到端成了空操作。现在让这些行通过,由更靠前的两道机制保证对话 tab
仍然干净:① 对话 tab 请求 `include='chat'`,后端就把 `activity` 行剔掉;② `ChatPanel.visibleTimeline`
按 `messageType === 'activity'` 把活动行排除在对话 tab 外(session 侧活动行靠这一道)。`:140` 只在
history 循环,session 循环无同类 filter,删除不影响 live session。用例:
`buildTimeline.test.ts` 断言 `{activity, message_bus}` history 行会出现在结果里(删了会回来的非空守卫)。

## 2026-08-04 — 空白历史行兜底过滤

history 循环跳过 `isBlankText(content) && !attachments?.length` 的行
（空白谓词统一在 lib/isBlankText.ts）：后端 strip 守卫上线前已写坏的
存量空白行只能靠这层挡住不出空气泡。带附件的空 content 行（纯附件消
息）保留——附件自己会渲染。这是兜底不是主修，主修在 chat_module 落库
守卫；不在 MessageBubble 里 early-return 藏行（那是把 bug 藏起来）。
隐式耦合：被 continue 的行同时退出下方去重索引（historyEventRoleKeys
/ historyByContentKey），同 event_id 的 session 副本将不再被去重——今
天无害（后端不再产空白行、存量空白行无活 session），若未来重新出现空
白行生产者要先想起这层。

## 2026-07-14 — carry `actionReason` through the same hop

`TimelineItem` gained `actionReason` and `toSessionItem` now copies it — the
exact same session→timeline hop that historically dropped `isError`/`warnings`
(see below) had also silently dropped the new `config_actionable` reason, so
the live actionable panel fell back to the generic "Run failed" popover. This
only matters on the SESSION path (a just-finished failed turn); on history
reload the failed turn renders via the failed-turn filter, not an assistant
error bubble. Pairs with the `actionReason` prop pass-through in
[[ChatPanel.tsx]] and the actionable render in [[MessageBubble.tsx]].

## 2026-07-03 — carry error state (isError/warnings) through the timeline

TimelineItem gained isError + warnings, and toSessionItem now copies them.
The May-2026 unified-timeline refactor defined TimelineItem WITHOUT these two
fields, so a failed turn lost its error flag on the session→timeline hop and
rendered as an innocuous message — the red error bubble/warning list (still
live in MessageBubble) silently stopped firing. This re-connects the data.

# buildTimeline.ts — pure history⊕session merge + dedup for the chat timeline

## Why it exists

The chat view shows one chronological conversation, but the data comes
from two independently-produced sources:

- **history** — the transcript in `instance_json_format_memory_chat`
  (ChatModule's per-instance JSON memory), via `getSimpleChatHistory`. **Not**
  the `events` table, and **not** `agent_messages` — that one is a tombstone
  with no writer, and mistaking it for the source is how the 0802 ordering
  report got misdiagnosed (see [[agent_message_repository]] 2026-08-05).
- **session** — live messages in `[[chatStore.ts]]` (the user prompt added
  on send; the assistant reply assembled at `stopStreaming` from the
  `reply_owner` tool args)

Once a turn finishes it exists in **both** — history has the persisted
copy, session still holds the live copy. Something has to drop the
session copy so the turn renders once. That "something" used to be a
~50-line block buried in a `ChatPanel` `useMemo`. It was extracted here
as a pure function specifically because the dedup has caused **two**
production bugs and needed to be unit-testable in isolation
(`__tests__/buildTimeline.test.ts`).

## The dedup — event_id first, content heuristic only as fallback

**Primary key: `(role, event_id)`.** Every message of a turn — the user
prompt and the assistant reply — is persisted with that turn's
`event_id`. `[[chatStore.ts]]` now stamps the SAME `event_id` onto the
session copies (`setCurrentRunId` backfills the user prompt from the
`run_started` frame / reconnect; `stopStreaming` stamps the assistant
reply). `${role}:${event_id}` is therefore an exact, formatting-immune
identity.

This replaced a `${role}:${content}` exact-string key. That key silently
missed whenever the session-assembled content and the DB-persisted
content drifted by even one character — and they ARE produced by
different code paths (session joins `reply_owner` args
with `\n\n`; the backend persists independently and sometimes *rewrites*
content, e.g. owner-notify substitution in `agents/chat_history.py`).
That drift made the latest reply occasionally render twice — the bug
this file fixes.

**Why not fall through from event_id to the content heuristic:** if a
session message HAS an `event_id` but history doesn't carry that
`(role, event_id)` yet (turn just finished, history hasn't reloaded), we
render the session copy and stop. event_id is authoritative — falling
through could false-positive against an unrelated row with the same
text, *or* false-negative and vanish the just-finished reply.

**Fallback — `(role, content)` + 5-min window + match-and-consume:** used
ONLY for messages with no `event_id` (legacy history rows; a session
message created before `run_started` landed). The *consume* step (splice
the matched history timestamp) preserves Bug-19 semantics: a user who
sends the exact same text twice gets each session copy paired one-to-one
with a history row — the retry is not swallowed.

## Upstream / Downstream

- **Called by**: `[[ChatPanel.tsx]]` — one `useMemo(buildUnifiedTimeline,
  [historyMessages, messages])`.
- **Depends on**: nothing but types — pure, no React, no store, no I/O.
- **Owns**: the `TimelineItem` type (moved here from ChatPanel).

## Gotcha

The two history index structures (`historyEventRoleKeys` set,
`historyByContentKey` map) are built from `items` *after* only the
history rows have been pushed and *before* the session loop. If you ever
reorder those phases, the session messages would index themselves and
dedup against each other — don't.
