---
code_file: src/xyz_agent_context/module/lark_module/lark_trigger.py
stub: false
last_verified: 2026-04-20
---

## 2026-04-20 change — uses `collect_run` + surfaces runtime errors (Bug 2)

`_build_and_run_agent` used to iterate `runtime.run()` directly and only
handled `MessageType.AGENT_RESPONSE`, silently dropping `ERROR` events.
That's Bug 2 — if the runtime failed to resolve LLM config the Lark
sender saw radio silence.

Now the trigger delegates to `agent_runtime.run_collector.collect_run`
and, when `collection.is_error` is true:

  1. Renders a user-friendly IM text via the module-level
     `format_lark_error_reply(error)` helper (not the raw developer
     message — the Lark sender is usually not the bot's owner and
     can't act on "slot is not configured").
  2. Sends the friendly text through `self._cli.send_message` so the
     chat acknowledges receipt of the user's message.
  3. Returns the same text so the Inbox outbound row records what
     happened.

A secondary `send_message` failure is logged but doesn't propagate — we
still return the text so Bug 10's Inbox fidelity is preserved.

## Why it exists

Bridges the gap between Lark/Feishu's real-time event stream and the
AgentRuntime pipeline.  Without it, agents have no way to receive and
respond to Lark messages.

## Design decisions

- **1 SDK WebSocket per app_id** — each bound bot gets its own
  `lark-oapi` WebSocket thread via `ws.Client.start()`.
  This keeps event streams isolated and allows per-bot backoff on
  reconnect (5 s → 120 s exponential).
- **Shared async worker pool** — all subscribe processes feed into a
  single `asyncio.Queue`; N workers consume from it.  Worker count
  scales dynamically: `base + 2 × subscriber_count`, capped at 50.
- **credential_watcher loop (10 s)** — hot-adds new bots and
  **hot-removes** deactivated ones without restart.  Compares DB
  state against running `_subscriber_tasks` each cycle.
- **Per-credential echo filtering** — `_bot_open_ids` is a
  `Dict[profile_name, open_id]` so every bot's own messages are
  filtered.  Two-layer check: `sender_type` (raw format) then
  `open_id` match (compact format).

## Upstream / downstream

- **Upstream**: `lark-cli` subprocess (WebSocket → NDJSON stdout),
  `LarkCredentialManager` (DB credentials).
- **Downstream**: `LarkContextBuilder` → `AgentRuntime.run()` →
  `_write_to_inbox` (bus_messages / bus_channels / bus_agent_registry /
  bus_channel_members).

## Gotchas

- `app_id` dedup means same-app multi-agent routing is still an open
  issue — the first credential per `app_id` wins.
- `AgentRuntime` is instantiated per message (no reuse) — acceptable
  now but worth pooling if init cost grows.
- `_adjust_workers` cancels excess tasks immediately; a worker in the
  middle of `_process_message` will only stop after its current await
  yields — no mid-message data loss.
- `_seen_messages` dedup dict is protected by `threading.Lock` because
  SDK callbacks run in a separate thread.
- `_subscribe_loop` patches `lark_oapi.ws.client.loop` — a fragile
  workaround for the SDK's module-level event loop capture.  May break
  on SDK updates.
- **Reply detection** (`_extract_lark_reply`) supports both V1
  (`lark_send_message` tool) and V2 (`lark_cli` with `+messages-send`/
  `+messages-reply` in command string). Uses `shlex.split` to extract
  `--text` value from the V2 command string.
- **Inbox outbound row stores the full reply verbatim** — earlier
  revisions wrote a hard-coded `"(Replied on Lark)"` stub, which
  erased the message content in the Inbox UI. The `bus_messages`
  outbound row now persists `agent_response` directly. The string
  `"(Replied on Lark)"` still appears in `_build_and_run_agent` as a
  legitimate fallback for the case where the caller could not parse
  out the reply text from the tool call — that scenario stays a
  placeholder on purpose.
