---
code_file: src/xyz_agent_context/module/wechat_module/wechat_module.py
stub: false
last_verified: 2026-07-10
---

## 2026-07-10 — early-feedback removed from get_instructions (moved to trigger)

The "ack early" block (and its `is_wechat_channel` gate) is gone from
`get_instructions`; it's now injected per-turn by the trigger
(`_early_feedback_prefix`, see [[channel_trigger_base]]). WeChat leaves
`react_tool_ref` unset → the base default (None) → message-only ack, so the
channel gate is no longer needed here.

## 2026-07-10 — PR #87 review: is_wechat_channel gate + shared render

Two review fixes: (1) the early-feedback block is now gated on
`WorkingSource.WECHAT` (was gated only on `source_message_id`, which the base
writes for ALL channels → a WeChat-bound agent handling a Lark turn wrongly saw
WeChat's "reply via wechat_send" ack — a cross-channel leak). (2) It's rendered
by the shared [[channel_reactions]] `render_early_feedback(tool_ref=None, …)`
(message-only variant — WeChat has no reaction API).

## 2026-07-10 — get_instructions surfaces early-feedback affordance

Operational prompt now includes an "Early feedback" block (when a
`source_message_id` is present): a generic SHOULD directive — for non-trivial
requests, ACK FIRST with a short `wechat_send` "on it" THEN do the work. WeChat
has no reaction API, so the ack is a message. Generic system-prompt rule, not
per-agent Awareness (rule #4); not a hard guarantee (rule #15).

## 2026-07-03 — handler registers `dedicated_trigger=True`

Marks wechat as owning a dedicated trigger process so MessageBusTrigger
won't re-dispatch `wechat_*` inbox rows (the 2026-07-03 double-dispatch
incident). See message_source_handler.py.md.

## Why it exists

WeChat's ``ChannelModuleBase`` subclass — the channel module for
personal-account WeChat via the iLink ("ClawBot") gateway. iLink is an
HTTP gateway (host ``https://ilinkai.weixin.qq.com``) that is PULL-only
(long-poll ``getupdates``, no webhook), single-token, no OAuth and no
manifest — so architecturally it is Telegram's twin, and this file
deliberately mirrors ``telegram_module.py``.

The module implements the channel surface the base expects:
``get_credential`` / ``send_to_agent`` / ``register_mcp_tools`` /
``get_instructions`` / ``build_extra_data``. The four deltas from
Telegram:

1. Binding is a **QR-scan flow** (Channels panel → ``backend/routes/
   wechat.py``), not a token paste — so there is **no ``wechat_bind``
   MCP tool**.
2. The agent replies via ``wechat_send(to_user_id, context_token,
   text)``.
3. Owner identity is the peer's wxid, **claimed on first DM** (opaque
   at bind — see the trigger).
4. v1 is **DM-only** (personal account, 1:1) — no groups.

## Design decisions

- **Registers a ``MessageSourceRegistry`` handler at import time.** This
  is load-bearing: without a registered handler named ``"wechat"`` whose
  ``user_reply_tool_names`` include ``("wechat_send",
  "send_message_to_user_directly")``, the agent's replies would be
  logged as generic **"Background activity"** rather than recorded as
  real user replies. ``_extract_wechat_reply`` pulls the user-visible
  text (``wechat_send.text`` / ``send_message_to_user_directly.content``)
  and returns ``None`` for any other tool call so non-replies don't
  pollute the inbox. The registration is wrapped in
  ``try/except ValueError`` so the import-time call is idempotent across
  re-imports.
- **Two prompt modes.** ``_NO_BIND_INSTRUCTION`` (no credential bound)
  walks the user to the Channels panel QR flow and tells the agent
  there's nothing for it to configure. The bound template adds a trust
  block + the reply instructions. Same shape as Telegram's
  two-mode prompt.
- **Iron rules state the channel's hard constraints.** (1) **plain text
  only** — WeChat renders no markdown, so ``*`` / backticks / ``#`` show
  up literally; no code fences, no bold markers. (2) **exactly one
  message per turn** via ``wechat_send`` — don't spam. (3) **1:1 DM
  only** — no groups in v1. These map directly to the trigger's
  text-only / PRIVATE-only parsing and the SDK's single-send reply path;
  keep the rule text in lockstep with that coverage.
- **Three-state trust block.** ``get_instructions`` renders one of:
  *no owner claimed yet* (treat sender as untrusted until the first DM
  claims ownership), *owner is the current sender* (may surface
  owner-private context), or *current sender is NOT the owner* (treat
  as a visitor, never disclose owner-private context or impersonate the
  owner). The owner-private gating is the whole point of resolving the
  wxid at first-DM time.
- **``send_to_agent`` needs a ``context_token`` via kwargs.** The
  cross-channel sender contract passes ``target_id`` (the peer wxid),
  but iLink also needs a ``context_token`` to address the conversation;
  it comes through ``kwargs`` (the reply path always carries it).
  Returns plain dicts, never raises — same contract as Telegram's
  ``send_to_agent``.
- **``build_extra_data`` computes ``is_owner_interacting``.** It reads
  the current ``sender_id`` out of ``ctx_data.extra_data["channel_tag"]``
  and compares to ``cred.owner_wx_id``; the result drives which of the
  three trust blocks renders. The dict it returns is what
  ``get_instructions`` reads back under ``ctx_data_key`` =
  ``"wechat_info"``.
- **MCP port 7835, ``priority=7``.** Continues the channel-port range
  (Lark=7830, Slack=7831, Telegram=7832, NarraMessenger=7833, Discord=7834,
  WeChat=7835).
  ``priority=7`` matches Telegram's slot ordering; reordering changes
  prompt section order — keep stable.

## Upstream / downstream

- **Upstream**: ``ChannelModuleBase`` (sender registry,
  ``hook_data_gathering`` template, MCP server creation glue).
- **Downstream**:
  - ``WeChatCredentialManager`` — credential CRUD (``get`` / ``unbind``
    / ``claim_owner`` / ``list_active``).
  - ``register_wechat_mcp_tools`` — the 3 MCP tools (``wechat_send`` /
    ``wechat_status`` / ``wechat_unbind``) on the FastMCP server.
  - ``wechat_sdk_client.send_text_once`` — the raw iLink HTTP send.
  - ``WorkingSource.WECHAT`` — enum entry tying WeChat-triggered events
    back through ``hook_after_event_execution``.
  - ``MessageSourceRegistry`` / ``MessageSourceHandler`` — reply
    recording (the "not Background activity" guard).
- **Binding flow lives elsewhere**: ``backend/routes/wechat.py`` drives
  the QR scan; this module never binds.

## Gotchas

- **The reply tool tuple and the MCP tool set must stay in sync.** If a
  new reply tool is added to ``_wechat_mcp_tools.py``, it must also be
  added to ``user_reply_tool_names`` here AND handled in
  ``_extract_wechat_reply`` — otherwise its output is logged as
  "Background activity" and the inbox loses the reply.
- The bound-state prompt embeds ``owner_wx_id`` / ``current_sender_id``
  from ``ctx_data.extra_data["wechat_info"]``. If ``build_extra_data``'s
  shape changes, the f-string renders empty without raising — eyeball
  it on rebind (same failure mode Telegram's mirror flags).
- There is **no ``wechat_bind`` tool by design.** A future maintainer
  "adding the missing bind tool for symmetry with Telegram" would be
  wrong — WeChat binding is a QR handshake the agent cannot perform.
- First-DM owner claim means a freshly bound account has **no owner**
  until someone DMs it; the trust block correctly treats that window as
  untrusted. Don't paper over it by defaulting an owner at bind time —
  the wxid genuinely isn't known then.
- ``priority=7`` is intentional. Not a free knob.
