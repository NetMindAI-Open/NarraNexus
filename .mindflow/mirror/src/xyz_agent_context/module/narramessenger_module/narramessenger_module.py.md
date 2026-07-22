---
code_file: src/xyz_agent_context/module/narramessenger_module/narramessenger_module.py
stub: false
last_verified: 2026-07-20
---

## 2026-07-21 — `_CLI_CAPABILITY` closing line clarified (writes ≠ read-only)

The closing "Sending stays on narra_reply/narra_send…" line was reinforcing an
agent's wrong belief that `narra_cli` is read-only (see [[_narramessenger_mcp_tools]]
2026-07-21). Reworded: only CHAT messages go elsewhere; explore *writes* and
everything else ARE `narra_cli` — "don't refuse a write as read-only, call the
tool and try".

## 2026-07-20 — `_CLI_CAPABILITY` instruction block

``get_instructions`` injects a ``_CLI_CAPABILITY`` block between ``_BEHAVIOUR``
and ``_IRON_RULES``. Companion to the [[_narramessenger_mcp_tools]] passthrough
work (2026-07-20). Reply/proactive/media guidance unchanged.

Reframed later the same day (**"### Operating NarraMessenger — narra_cli +
narra_guide"**, was "### Reading room context"): an agent asked about "publish"
and couldn't tell whether it was a NarraNexus / NarraMessenger / other feature.
Fixes:
- the block now opens with what NarraMessenger *is* (the IM network) and frames
  ``narra_cli`` as "how you operate it", not just "read room context";
- gives a **capability overview** incl. **explore/publish** (official-agents-only;
  server returns ``official-agent-required``) with an explicit
  ``"publish a post" = narra_cli("explore publish ...")`` mapping — the missing
  signal that caused the confusion;
- points to ``narra_guide()`` / ``<domain> --help`` for exact flags;
- adds an **authority-on-conflicts** clause: the NarraNexus platform is SSOT for
  identity / tokens / permissions (never pass ``--token*``; ignore runtime.md
  token/endpoint self-management guidance); ``narra_guide`` / runtime.md is only
  for narra-cli command *syntax*, not platform policy.

## 2026-07-03 — Matrix-native send + stale-prompt sweep

Part of the outbound send unification (see [[_matrix_send]] / [[_narramessenger_mcp_tools]]).

- **`send_to_agent`** (ChannelSenderRegistry) repointed from `/chat/send` to
  Matrix `room_send`. This module no longer imports `NarramessengerClient` for
  sending (only bind/status uses it now).
- **MessageSourceRegistry handler**: the memory extractor now recognises
  `narra_reply` / `narra_send` (both carry text in the `text` arg) and DROPS
  `send_message_to_user_directly` — that generic tool is the OWNER channel, not
  a room reply, so logging it as a NarraMessenger reply was wrong.
- **Prompt sweep (behaviour-lied fixes, cf. the group-visibility fix below)**:
  `_BEHAVIOUR` point 3 said "images/files… you cannot open them" — false since
  the Phase-3 receive landed; now it tells the agent files are downloaded to the
  workspace + `Read` them, and `narra_send_media` to send. `_reply_action_block`
  dropped the dead `invocation_id` (Matrix has none — the trigger knows the
  room); `_PROACTIVE_ACTION` reworded. `current_invocation_id` removed from
  `build_extra_data` (write-only after the reply-block change).

## 2026-07-02 — `_BEHAVIOUR` group-visibility line rewritten

The old rule 2 read "In group rooms you are only invoked when
@-mentioned" and stopped there. That was accurate under Narra-strict
policy (group non-@ events were denied and never reached memory), but
after the `SILENT_BYPASS_AUTHORIZE` override (see
[[matrix_trigger.py]] owner override note), the agent's chat_history
DOES contain silent-ingested group messages — the LLM was hallucinating
"I can't see non-@ messages" because the prompt told it so.

Rewritten rule 2 now says explicitly: "You SEE every message (silently
ingested into your conversation memory even when you weren't summoned),
but you only REPLY when directly @-mentioned." It also names the
`silent=true` metadata marker so the LLM can distinguish
silently-ingested rows from directly-addressed turns when the
distinction matters.

Verified live: after this change, `agent_62cf67080ad4` on a group
non-@ ingest (already in chat_history as `silent=True`) correctly
answers "yes, I saw that message" when @-mentioned about it. The
memory infrastructure was fine; only the prompt lied.

## 2026-07-03 — handler registers `dedicated_trigger=True`

MessageBusTrigger derives its do-not-redispatch channel prefixes from this
flag (see message_source_handler.py.md, 2026-07-03).

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
