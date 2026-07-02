---
code_file: src/xyz_agent_context/module/narramessenger_module/narramessenger_module.py
stub: false
last_verified: 2026-07-02
---

## Why it exists

The agent-facing surface of the NarraMessenger channel (`ChannelModuleBase`).
Owns the sender (`send_to_agent` → `/chat/send`, registered in
`ChannelSenderRegistry`), the `narra_reply`/`narra_send`/`narra_bind`/
`narra_status` MCP tools, the per-turn `get_instructions` (system-prompt
behaviour), and `build_extra_data` (trust signal + threaded ids). Mirrors
`telegram_module.py`.

## 2026-06-18 — prompt refactor + reply/send split

- Prompt text extracted to module-level constants (`_SETUP_INSTRUCTION`,
  `_BEHAVIOUR`, `_IRON_RULES`, `_PROACTIVE_ACTION`) — lark/telegram convention;
  `get_instructions` only assembles named sections (`_trust_block` +
  `_reply_action_block` are the dynamic, id-interpolated pieces).
- `get_instructions` renders by `working_source`: REPLY mode (ws ==
  NARRAMESSENGER) shows an **identity block** with sender/room_id/**invocation_id**
  and tells the agent to call `narra_reply(invocation_id, text)`; otherwise the
  proactive `narra_send(room_id, text)` block.
- `build_extra_data` threads `current_invocation_id` (parsed from
  `trigger_id = "narramessenger_<invocation_id>"`) + `current_room_id` into
  `narramessenger_info`, so the agent can copy the invocation_id into
  `narra_reply` (same as it copies room_id). This is what fixes the timeout.

## Design decisions

- **`send_to_agent` and `narra_send` both go through `/chat/send`** (bearer,
  `txn_id`=uuid4, no reply deadline). The agent replies by calling `narra_send`
  with the inbound `room_id`; the registry path serves composite/proactive
  sends.
- **`MessageSourceRegistry` handler** (`name="narramessenger"`, reply tools
  `narra_send` / `send_message_to_user_directly`) so ChatModule captures
  NarraMessenger replies into chat history instead of logging "Background
  activity". Registered at import, idempotent.
- **`get_instructions` is short (~telegram-sized), NOT lark's 600 lines.**
  Identity + how-to-reply + DM/group behaviour + owner trust block + an
  explicit **output-hygiene iron rule**: never emit identity/trust/instruction
  text as a `narra_send` reply. This directly targets a real bug observed on a
  cloud responder ("I am X's agent. X has full access to my account.").
- **Trust signal**: `owner_matrix_user_id == channel_tag.sender_id` →
  `is_owner_interacting`. Same model as Slack/Telegram/Lark.
- **`owner_matrix_user_id` is populated by the trigger, not this module**
  (2026-07-02, X2/X3 fix). This module only reads it; it never writes it.
  `do_bind` can't learn the binder's identity (see `_narramessenger_service.md`),
  so `NarramessengerTrigger._maybe_claim_owner` claims the first sender in
  the bind room as owner on the first inbound message and persists it via
  `NarramessengerCredentialManager.update_owner`. Before this fix
  `owner_matrix_user_id` was permanently empty post-bind, so
  `is_owner_interacting` was always `False` and `_trust_block` always
  rendered "No owner is registered" — the agent could never recognize its
  own owner.

## Upstream / downstream

- **Upstream**: `ChannelModuleBase` → `XYZBaseModule`.
- **Registers**: sender (via base `__init__`), MessageSourceRegistry handler.
- **Calls**: `NarramessengerClient.chat_send`, `NarramessengerCredentialManager`.
- **Fed by**: `NarramessengerTrigger._maybe_claim_owner` (owner identity —
  see that trigger's mirror doc for the write side of the X2/X3 fix).
- **MCP**: port 7833, server name `narramessenger_module`.

## Gotchas

- `get_config` is a `@staticmethod` (like all channel modules); pyright flags
  the override as incompatible with the base instance method — this is an
  accepted codebase-wide pattern (identical on telegram), not a bug.
- If the v1 reply policy changes from `/chat/send` to `/reply`, update both
  `send_to_agent` and the `narra_send` tool, and revisit `extract_output` in
  the trigger.
