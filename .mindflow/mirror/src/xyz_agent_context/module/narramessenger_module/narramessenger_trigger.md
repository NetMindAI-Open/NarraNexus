---
code_file: src/xyz_agent_context/module/narramessenger_module/narramessenger_trigger.py
stub: false
last_verified: 2026-07-02
---

## Why it exists

Inbound trigger for NarraMessenger built on `ChannelTriggerBase`. Structurally
a twin of `TelegramTrigger` (both are long-poll), but polling the
NarraMessenger gateway instead of Telegram `getUpdates`. The base owns dedup /
worker pool / credential watcher / audit / inbox; this subclass fills the
abstract surface and the gateway poll loop.

## Design decisions

- **`connect()` is the gateway loop**: `POST /connect` once to activate, then
  `GET /invocations/poll?timeout=30000` forever, yielding invocation dicts.
  `no_invocation` → short idle sleep; `update_guide_required` → ack the version
  programmatically (we pin the contract, we do NOT run the self-update
  document). Permanent errors (401/409 via `is_permanent_api_error`) propagate
  so the base watcher disables the credential; transient errors propagate for
  backoff/reconnect.
- **No authorize-event step.** Gateway invocations are already filtered +
  authorized by the platform, so unlike a Direct-Matrix runtime there's no
  per-event permission check to add. (The base class itself needed no change;
  see the owner auto-claim override below, which is a `_process_message`
  override on THIS subclass, not a base-class change.)
- **Owner auto-claim overrides `_process_message`** (2026-07-02, X2/X3 fix).
  `do_bind` (`_narramessenger_service.py`) can only ever learn the AGENT's own
  Matrix identity from the connect response (`matrixUserId`/`principalId`/
  `roomId`) — never the binder's. So `owner_matrix_user_id` was permanently
  empty after every bind, which made `NarramessengerModule.build_extra_data`
  always compute `is_owner_interacting=False` (X2) and `_trust_block` always
  render "No owner is registered" (X3) — the agent could never recognize its
  own owner. The fix: the first message in the **bind room**
  (`credential.bind_room_id`, captured from the connect response's `roomId`)
  claims its sender as owner. The bind room is a 1:1 Matrix DM the platform
  creates for whoever ran the bind flow, so "first sender in that exact room"
  is equivalent to "the person who bound this agent" — same trust argument as
  Telegram's `_maybe_resolve_owner`, just keyed on room identity instead of a
  username lock (NarraMessenger's bind flow has no username to lock onto).
  Gate logic lives in `_should_claim_owner` (pure, easily testable); the
  write + in-memory mutation lives in `_maybe_claim_owner`.
- **Claim gate excludes the agent's own identity independently of `is_echo`**
  (2026-07-02, PR review follow-up). `_maybe_claim_owner` runs BEFORE
  `super()._process_message` — i.e. before the base class's `is_echo` filter
  ever executes — so `_should_claim_owner` also checks `message.sender_id !=
  credential.matrix_user_id` itself. Without this, a platform that ever
  echoes the agent's own `/chat/send` output back into the bind room as an
  invocation would let the agent permanently claim itself as its own owner
  (the claim never re-fires once `owner_matrix_user_id` is set).
- **`load_conversation_history=False`.** History rides INLINE in each
  invocation (`context` / `group_context.history_messages`); the context
  builder reads it from `ParsedMessage.raw`, so the base's per-room history
  loader is off.
- **`extract_output` scrapes `narra_send` tool-call `text`, NOT
  `output_text`** — same chain-of-thought-leak guard as Telegram/Slack.
- **`message_id` = group `trigger_message.event_id` when present, else
  `invocation_id`** — keeps the dedup key stable across any re-delivery.
- **No `fetch_attachments`** — NarraMessenger is text-only for the agent.

## Upstream / downstream

- **Upstream**: `ChannelTriggerBase`.
- **Calls**: `NarramessengerClient.connect/poll/ack_update_guide`,
  `NarramessengerCredentialManager.list_active/update_owner`,
  `NarramessengerContextBuilder`.
- **Schemas read**: `ParsedMessage`, `ChatType`, `WorkingSource`.
- **Feeds**: the owner claim written by `_maybe_claim_owner` is read back the
  SAME turn by `NarramessengerModule.build_extra_data` (via
  `get_credential` → fresh DB fetch) — see that module's mirror doc for the
  `is_owner_interacting` / trust-block consumer side.

## Gotchas

- DM invocations carry no timestamp; `timestamp_ms` is 0 for DMs (only group
  `trigger_message.origin_server_ts` is available). The historic-replay dedup
  baseline therefore can't gate DMs by age — the TTL window + per-invocation
  pop is the guard.
- Sender `display_name` in the payload is unreliable (often equals the
  matrix_user_id); `resolve_sender_name` deliberately returns the id rather
  than calling an API.
- **Owner claim only fires once per agent, ever.** If a credential's
  `bind_room_id` is empty (e.g. a row written by `scripts/seed_narramessenger_
  credential.py` without going through `do_bind`) auto-claim silently never
  fires — `_should_claim_owner` requires a non-empty `bind_room_id`. Seed
  scripts / manual DB inserts that need an owner must call
  `NarramessengerCredentialManager.update_owner` directly.
