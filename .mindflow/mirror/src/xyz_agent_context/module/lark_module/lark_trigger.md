---
code_file: src/xyz_agent_context/module/lark_module/lark_trigger.py
stub: false
last_verified: 2026-04-21
---

## 2026-04-21 hardening pass — reliability + observability

After a user-reported incident ("bot went silent for hours, then
replied to 5 old messages with no log access to diagnose") we walked
the whole trigger and closed every reliability gap we could identify,
then added an audit black-box so the NEXT incident can be diagnosed
without EC2 shell access.

### Fixes (see BUG_FIX_LOG entry for full rationale)

- **H-1 — backoff resets on healthy sessions** (`_compute_next_backoff`):
  previous loop compounded backoff every disconnect toward the 120 s
  cap even after hours of clean session. Now resets to 5 s whenever
  the just-ended WS session lasted ≥ 60 s.
- **H-2 — cred gatekeeper** in `_process_message`: events from a bot
  that has been unbound (subscriber removed, SDK thread still alive)
  are dropped before reaching the agent. `_stop_subscriber` also
  clears the `_bot_open_ids` cache for that cred.
- **H-3 — dedup fail-open actually works**: `LarkSeenMessageRepository.mark_seen`
  now re-raises non-UNIQUE exceptions; trigger's `_check_and_classify_event`
  turns that into a loud fail-open (`layer=db_fail_open`). Previously
  the repo fail-closed turned transient DB errors into silent message
  loss.
- **H-4 — worker self-heal**: `_prune_dead_workers` called from the
  watcher loop drops any worker task that ended unexpectedly, letting
  `_adjust_workers` rebuild the pool.
- **H-5 — historic filter uses last WS reconnect as baseline**: a long
  WS disconnect followed by reconnect releases Lark's backlog of
  events created during the dark window; those are replays, not fresh
  traffic. Baseline is now `max(startup_time, last_ws_connected_at)`.
- **M-6 — `_bot_open_ids` keyed by `(agent_id, app_id)`**: a rebind of
  the same agent to a different app no longer reuses the old bot's
  open_id for echo detection.
- **M-7 — per-message total timeout**: `_worker` wraps
  `_process_message` in `asyncio.wait_for(PROCESS_MESSAGE_TIMEOUT_SECONDS=1800)`.
  `collect_run`'s idle timeout covers stream silence only, not
  total wall-clock.
- **M-9 — `_WS_LOOP_PATCH_LOCK`** serialises the
  `lark_oapi.ws.client.loop` mutation across concurrent reconnects.
- **M-10 — inbox write failure fallback**: `_write_to_inbox` failures
  now write an `inbox_write_failed` audit row with the original
  message and agent reply, so content isn't silently lost.
- **L-12 — `_sanitize_display_name` strips C0/C1 control characters**:
  closes a prompt-injection seam via Lark nicknames with newlines.
- **L-13 — periodic cleanup**: dedup + audit retention cleanup runs
  from the watcher every `CLEANUP_INTERVAL_SECONDS` (24 h) instead
  of only at startup.

### Observability additions

- **`lark_trigger_audit` table** (schema in
  `utils/schema_registry.py`, repo in
  `repository/lark_trigger_audit_repository.py`): every lifecycle
  decision writes a row — ingress accept/drop (with dedup layer),
  echo drop, unbound drop, WS connect/disconnect/backoff, subscriber
  start/stop, worker error/timeout, inbox-write failure, heartbeat.
  30-day retention.
- **`/healthz` endpoint** on port `47831` (FastAPI + uvicorn, inside
  the trigger process). Snapshot of running state, subscriber/worker
  counts, queue depth, uptime, last WS connect wallclock, and 1-hour
  event-type counts. Container-internal — `docker exec narranexus-lark
  curl -s localhost:47831/healthz`.
- **Heartbeat audit row every 10 min**: absence in the audit table =
  trigger was stuck or down during that window.

---

## 2026-04-20 change — durable dedup + startup filter (Bug 27)

Lark delivers events at-least-once: WebSocket reconnects, missed acks,
or process restarts cause the server to re-push the same `message_id`.
The previous design (in-memory `dict` with 60s TTL) could not survive
either scenario — Xiong observed the agent answering the same user
message twice, once right away and once ~an hour later after a restart.

Three-layer defence replacing the single in-memory check:

  1. **Startup-time filter** (`HISTORY_BUFFER_MS = 5 min`): events whose
     Lark `create_time` is older than `startup_time - HISTORY_BUFFER_MS`
     are replays from before this process started. Dropped outright
     without touching the DB. 5-min buffer keeps "user sent right
     before restart" traffic flowing.
  2. **In-memory hot cache** (now `DEDUP_TTL_SECONDS = 600`, was 60):
     unchanged contract but with a longer window, so routine bursts of
     Lark re-deliveries within a single WS session never need to go
     to DB.
  3. **Durable DB gate** via `LarkSeenMessageRepository.mark_seen`:
     atomic INSERT on the `lark_seen_messages` table — survives
     process restart. See `repository/lark_seen_message_repository.py`.

The full check lives in `_should_process_event`; the SDK callback
(`on_message`) now only converts the event to a dict and hands it to
`_dedup_and_enqueue`, which runs the full chain on the asyncio loop.
This refactor keeps the SDK thread fast and centralises the dedup
policy for tests.

`start()` also runs `cleanup_older_than_days(DEDUP_RETENTION_DAYS=7)`
once on startup to bound table growth.

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
