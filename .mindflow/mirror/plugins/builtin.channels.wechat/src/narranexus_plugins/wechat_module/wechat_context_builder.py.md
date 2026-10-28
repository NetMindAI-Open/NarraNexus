---
code_file: plugins/builtin.channels.wechat/src/narranexus_plugins/wechat_module/wechat_context_builder.py
stub: false
last_verified: 2026-08-19
---

## 2026-08-19 — 部署窗口回落读（存量记忆不丢）

同 telegram：`get_conversation_history` 新表查空时回落读旧 `bus_messages`（`channel_id=wechat_{chat_id}`），按 agent 隔离（本 bot 回复 + `wechat_user_*`，排除他 bot），与 wipe 对齐，回填后删除。

## 2026-08-17 — 历史改读 inbox 记录

同 [[telegram_context_builder]]：iLink 没有历史 API，历史来自本地记录，该记录已从
`bus_messages` 搬到 `inbox_thread_messages`，读取方跟着搬。不搬 = 微信失忆。

`is_bot` 判别改用 `direction` 列。微信尤其依赖记录层的时序保证——它的消息**自身没有
时间戳**（`timestamp_ms == 0`），一轮内 inbound/outbound 的 1 微秒错位是唯一的排序依据。

## 2026-08-06 — `room_type` 成了行为开关；新增 `reply_kwargs()`

`room_type` 改用 `ROOM_TYPE_DIRECT` 常量（值不变）。**语义变了**：它不再只是
prompt 里「Conversation Type」那行，而是选择注入哪份通讯协议。因为本文件把它
硬编码成私聊，微信**每一轮**原先都在吃「默认不回复」+ 群聊纪律——0802「发消息
无反应」工单的根因之一（详见 `channel_prompts.py.md` 同日条目）。所以下面那条
「若将来支持微信群聊必须改这里」的提醒，代价从「显示不准」升级成「群里乱发」。

新增 `reply_kwargs()` 返回 `{"context_token": ...}`：iLink 寻址一个会话要
inbound 的 context_token，而平台侧兜底投递（`step_3` 的 `no_reply_im_dm`）
走 `ChannelSenderRegistry` 而不是模型的工具调用，所以得把这个 token 经通用信封
交出去。token 为空时投递会被 iLink 拒——那时兜底放弃并保持沉默，不写「已回复」。

## 2026-07-03 (final) — emoji warning reverted

The reply-instruction emoji ban added earlier today is removed: the drops
were caused by the send payload's missing `client_id` (see
wechat_sdk_client.py.md), not by emoji. Instruction is back to the
markdown-only caveat.

## Why it exists

WeChat-side implementation of ``ChannelContextBuilderBase``. Builds the
per-turn context the agent sees when an iLink (personal-WeChat) message
lands: sender identity, room metadata, the inbound body, conversation
history, and the exact ``wechat_send`` invocation the agent must use to
reply.

Mirrors ``telegram_module/telegram_context_builder.py``. iLink, like
the Telegram Bot API, exposes **no server-side history endpoint**, so
the conversation-history path falls back to the local database.

## Design decisions

- **History comes from the local ``bus_messages`` table, keyed
  ``channel_id = f"wechat_{chat_id}"``.** The iLink gateway has no
  ``conversations.history`` equivalent — a bot only sees messages that
  arrive while it long-polls. So ``get_conversation_history`` reads the
  rows ``ChannelInboxWriter`` already persisted under
  ``wechat_{to_user_id}`` (here ``chat_id`` IS the ``to_user_id`` of the
  DM partner). Telegram returns ``[]`` unconditionally for the same
  "no history API" reason but does **not** read local history; WeChat
  goes one step further and surfaces the local turns, because for a
  1:1 personal DM the local ``bus_messages`` rows ARE the full
  conversation.
- **History is best-effort and never raises.** The fetch is wrapped so a
  DB error logs a warning and returns ``[]`` rather than failing the
  turn. History is a nicety; the inbound message and reply contract are
  what must always be present.
- **History over-fetches then trims.** It pulls ``max(limit + 5, 10)``
  rows ``ORDER BY created_at DESC``, drops the current inbound message
  (matched on ``message_id`` so the agent doesn't see its own trigger
  echoed as history), normalises sender to ``"Me (bot)"`` vs the DM
  partner, re-orders oldest-first, then trims to ``limit``. The +5 / 10
  headroom absorbs the dropped current message and any bot rows.
- **``room_type = "Direct Message"`` always.** v1 is personal-account,
  1:1 DM only — there is no group path. This is hard-coded rather than
  derived, because the integration has no group concept to derive from
  (contrast Telegram, which infers group vs DM from the ``chat_id``
  sign).
- **``get_room_members`` returns ``[]``.** 1:1 DM — no member set to
  enumerate.
- **``reply_instruction`` hand-builds the ``wechat_send`` call.** It is
  pre-formatted with the actual ``to_user_id`` and ``context_token``
  pulled from the inbound message's ``raw``, so the agent calls
  ``wechat_send(to_user_id, context_token, text)`` without re-deriving
  them. The ``context_token`` is the gateway's per-inbound reply token —
  it must be echoed back on send or the gateway rejects the reply
  (stale-token ``ret != 0``).
- **The plain-text warning lives inline in ``reply_instruction``.**
  WeChat has no markdown rendering, so asterisks / backticks show up
  literally. The warning is placed at the call site (not only in the
  system prompt) so it reaches the agent where it actually composes the
  reply. The instruction also pins "Send exactly ONE message".
- **``send_tool_name = "wechat_send"``.** The
  ``ChannelContextBuilderBase`` contract surfaces the canonical send
  tool by name so the templated ``message_info`` can reference it.
  Telegram uses ``tg_cli``; Slack uses ``slack_send``; Lark uses
  ``lark_cli``.
- **``room_name`` left empty.** Personal-DM partners don't carry a room
  title; the renderer treats empty string as "use ``room_id``" (the
  ``to_user_id``).
- **Holds raw ``ParsedMessage`` + ``WeChatCredential``, reads lazily.**
  No copy / transform of fields, so future ``ParsedMessage`` extensions
  appear without touching this file.

## Upstream / downstream

- **Upstream**: ``ChannelContextBuilderBase``.
- **Constructed by**: ``WeChatTrigger`` (create-context-builder path),
  which passes the ``db_client`` needed for history.
- **Reads**: ``ParsedMessage``, ``WeChatCredential``, and the
  ``bus_messages`` table.

## Gotchas

- **History silently empties if ``db_client`` is missing.** The
  constructor allows ``db_client = None``; with no db (or no
  ``chat_id``) ``get_conversation_history`` returns ``[]`` with no
  warning. If history looks unexpectedly empty, check the trigger
  actually threaded the db client in.
- **``channel_id`` key must match what ``ChannelInboxWriter`` writes.**
  History reads ``wechat_{chat_id}``; if the inbox writer ever changes
  its channel-id scheme, history goes silently empty (the fetch
  succeeds, returns zero rows). Keep the two in sync.
- **``context_token`` is per-inbound and expires.** It's read from the
  inbound ``raw`` and baked into ``reply_instruction``. A reply that
  reuses a stale token (e.g. the agent replies to an old turn) will be
  rejected by the gateway with ``ret != 0`` and surface as a failed
  send.
- **``room_type`` is hard-coded "Direct Message".** If WeChat group
  support is ever added, this becomes a real branch — today it is
  correct precisely because v1 is DM-only.
