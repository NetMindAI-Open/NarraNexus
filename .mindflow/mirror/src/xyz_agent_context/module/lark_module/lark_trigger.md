---
code_file: src/xyz_agent_context/module/lark_module/lark_trigger.py
stub: false
last_verified: 2026-05-21
---

## 2026-05-21 — `resolve_sender_name` resolves via owner user token

Every sender was logging as "Unknown" because `get_user --as bot` can't
read names (bot lacks `contact:user.base:readonly`). The agent then
hallucinated identities (famously calling user "kz" "Zehua" from its
roster). Fix in two parts:

- `get_user` now uses `--as user` (see `lark_cli_client.md`).
- `resolve_sender_name` gates on `credential.user_oauth_ok()`: an agent
  that never completed the three-click user auth has no user token, so
  we return "Unknown" immediately instead of spawning a CLI call that can
  only fail. This is the accepted boundary — bot-only binds stay
  anonymous until the owner finishes Click 3.

## 2026-05-19 (PM) — `_ws_loop_exception_filter` now `loop.stop()`s instead of swallowing

### Incident that exposed it

EC2 lark container 2026-05-18T19:16 UTC: ~10 WS connections to
`msg-frontier-sg.larksuite.com` dropped within 8 minutes with
`keepalive ping timeout` / `Connection reset by peer`. **From 19:24
through the next day 05:19** — 10+ hours — the container produced
**zero `_process_message` log lines and zero `lark_trigger_audit` rows
of any transport kind** (no `transport_disconnected`, no
`transport_backoff`, no `transport_connected`). The container `docker
ps` reported `Up`. The user's bot ignored every message.

Resolved by `docker restart narranexus-lark` (which re-spawned
subscribers from a clean DB scan). Root cause discovery + code fix
below.

### Root cause — what the AM 2026-05-19 entry got wrong

The H-6 part 1 design (and the AM 2026-05-19 filter that followed it)
both assumed that with `auto_reconnect=False` the SDK's `start()` would
**return** on first WS disconnect, the daemon thread would die,
`t.is_alive() == False` would trigger the outer reconnect. **That
assumption is wrong** given the SDK's actual structure:

```python
# lark_oapi/ws/client.py:112-127
def start(self) -> None:
    try:
        loop.run_until_complete(self._connect())  # creates _receive_message_loop
                                                  # via loop.create_task (fire-and-forget)
    except ...:
        ...
    loop.create_task(self._ping_loop())           # fire-and-forget
    loop.run_until_complete(_select())            # blocks forever (see below)
```

```python
# lark_oapi/ws/client.py:62-64
async def _select():
    while True:
        await asyncio.sleep(3600)
```

When WS drops, `_receive_message_loop` raises with no awaiter — that's
exactly the "Task exception was never retrieved" the AM filter was
swallowing. **But the thread doesn't die because `_select()` keeps
sleeping.** The outer `while t.is_alive(): await asyncio.sleep(1)` poll
in `_subscribe_loop` never exits → no backoff, no reconnect, no audit
row, no log — exactly the 11-hour silence we observed.

Our filter was actively making it worse: by swallowing the
fire-and-forget task's exception, we removed even the noisy "Task
exception was never retrieved" trace that would otherwise have hinted
at the problem in logs.

### Fix

`_ws_loop_exception_filter` now calls `loop.stop()` (in addition to
not passing to `default_exception_handler`) when the exception is
`ConnectionResetError` / `ConnectionError` / `OSError` / any
`websockets.*` exception. `loop.stop()` runs at the next iteration of
the loop, causing `loop.run_until_complete(_select())` to abort with
`RuntimeError`. `ws_client.start()` then propagates that to the
`run_ws` thread wrapper, the daemon thread exits, and the outer
`_subscribe_loop` polling sees `t.is_alive() == False` and walks the
existing backoff + reconnect path (with proper `transport_disconnected`
audit row).

Unknown / non-connection exceptions still go through the default
handler so real bugs stay loud.

### Tests

- `tests/lark_module/test_ws_exception_filter.py`
  - Unit tests: 3 connection-class exception types each assert
    `loop.stop_called is True` AND default handler not invoked.
  - 2 pass-through tests assert `loop.stop_called is False` AND
    default handler IS invoked.
  - 1 integration test runs a real asyncio loop with an SDK-shaped
    `_select()` blocker; schedules a `ConnectionResetError` in a
    fire-and-forget task; asserts the loop terminates within 2 s
    (pre-fix: 60+ s).

### Followup — H-6 part 1 mirror entry has a load-bearing wrong claim

The "2026-04-27 — H-6 (part 1)" section below still says "On disconnect
the SDK now `raise`s instead of swallowing — `ws_client.start()`
returns". That claim **does not hold** under the SDK's actual code path
— it's left in place as a historical record of the design intent, but
do not treat it as accurate documentation. The behaviour the team
**thought** they were getting from `auto_reconnect=False` only actually
materialises with the 2026-05-19 PM `loop.stop()` change above.

## 2026-05-19 (AM, partially superseded) — filter WS disconnect noise on fresh_loop

`_subscribe_loop` now installs `_ws_loop_exception_filter` on the
per-thread `fresh_loop` before `ws_client.start()`. The SDK creates
fire-and-forget `loop.create_task(...)` jobs for incoming message
handling and reconnect plumbing; on WS reset they raise
`ConnectionResetError` / `OSError` / `websockets.*` exceptions with no
awaiter, and the default asyncio handler dumps "Task exception was
never retrieved" + traceback per occurrence.

**Superseded by the PM 2026-05-19 entry above**: the original
intent was "outer loop owns reconnect", but the outer loop never
actually fired because the SDK thread never died. The filter now also
calls `loop.stop()` to force-terminate the SDK thread on these
exceptions. The "log noise reduction" effect of swallowing is
preserved.

## 2026-05-19 — register message_read no-op processor

`_subscribe_loop` used to register only `register_p2_im_message_receive_v1`.
Lark pushes `im.message.message_read_v1` (read receipts) by default; the
SDK then logged an ERROR per event ("processor not found, type:
im.message.message_read_v1"). We extracted the handler build into a
static `_build_event_handler(on_recv, on_read)` and now register a no-op
read-receipt processor alongside the real receive handler. We never act
on read receipts — silencing the SDK noise is the only purpose.

The build helper is staticmethod so the test in
`tests/lark_module/test_message_read_handler.py` can introspect
`handler._processorMap` without standing up a WebSocket.

## 2026-05-08 — Phase 2: refactor onto `ChannelTriggerBase`

`LarkTrigger` is now a subclass of `xyz_agent_context.channel.channel_trigger_base.ChannelTriggerBase`.
The channel-agnostic 80% of the previous monolith is gone — Worker pool,
credential watcher, audit log, dedup store, inbox writer, and start/stop
machinery all inherit from the base. What remains here is the Lark-only
20%: SDK threading + `_ThreadLocalLoopProxy` (H-6), bot open_id cache
(M-6), `lark_cli` tool-call output extraction, IM-friendly error rendering.

### What inherits from `ChannelTriggerBase`

- `start()` / `stop()` lifecycle (Lark `start()` overrides to inject
  Lark dedup tunables + boot the `/healthz` server)
- `_credential_watcher` (calls our `load_active_credentials`)
- `_adjust_workers` / `_prune_dead_workers` / `_desired_worker_count`
- `_audit` helper writing to `channel_trigger_audit` (channel="lark")
- `_maybe_heartbeat` / `_run_cleanup`
- `_dedup_store` is a `ChannelDedupStore` writing to
  `channel_seen_messages` (channel="lark")

### What's overridden / Lark-specific

- `_subscribe_loop` — Lark's SDK threading model needs daemon threads +
  fresh per-thread asyncio loops (`_ThreadLocalLoopProxy` + H-6 fix). The
  base's `async for raw in connect()` shape doesn't fit; we override.
- `connect()` — abstract method satisfied by `raise NotImplementedError`;
  Lark drives via `_subscribe_loop` directly.
- `parse_event(raw)` — converts dict to ParsedMessage, stashes raw in
  `.raw` for `is_echo` to read `sender_type`.
- `is_echo(message, credential)` — two-layer check (sender_type +
  bot open_id cached on `(agent_id, app_id)`, M-6).
- `extract_output(result, message, credential)` — Lark scrapes
  `lark_cli` tool-call args (agent doesn't emit text directly).
- `format_error_reply(error)` — IM-friendly text via
  `format_lark_error_reply` (sender often isn't the agent owner).
- `_build_and_run_agent` — accepts both old 7-arg signature (legacy
  tests) and new (cred, message, sender_name) signature.
- `_worker` — defensive `message.message_id` extraction handling both
  dict and ParsedMessage on the queue (legacy test surface).
- `_stop_subscriber` — clears `_bot_open_ids` cache (M-6) on top of
  base's stop logic.

### Backward-compat shims kept for the 146 existing Lark tests

- `_dedup_and_enqueue(cred, dict)` — runs dedup + audit + enqueues a
  ParsedMessage (writes the rich INFO log + content_preview audit
  details on entry, before the dedup decision).
- `_check_and_classify_event(dict)`, `_should_process_event(dict)` —
  delegate to the base's `_dedup_store.classify`. Sync baseline from
  `_startup_time_ms` and `_last_ws_connected_wallclock_ms` on every
  call (monotonic) so tests that poke `_startup_time_ms` directly
  still get the right historic-replay behaviour.
- `_process_message(cred, dict_or_msg, worker_id=0)` — accepts both
  shapes. Production uses ParsedMessage; legacy tests use dict.
- `_seen_repo`, `_audit_repo`, `_seen_messages`, `_seen_lock`
  property shims.
- `_write_to_inbox(cred, ...)` — delegates to `ChannelInboxWriter`.
- `_sanitize_display_name` — alias to base's `sanitize_display_name`.
- `_is_echo(cred, raw_dict, sender_id)` — legacy signature wrapper.

### Tables: writes go to `channel_*` now

- New writes: `channel_seen_messages`, `channel_trigger_audit` (with
  `channel="lark"`).
- Legacy `lark_seen_messages` and `lark_trigger_audit` tables remain in
  `schema_registry.py` for safe rollback. Tests that explicitly
  construct `LarkSeenMessageRepository(db)` / `LarkTriggerAuditRepository(db)`
  and assign them to `_seen_repo` / `_audit_repo` still work via the
  property shims (data lands in legacy tables for those tests). Phase 2.5
  cleanup PR drops the legacy tables + shims after the migration window.

---

## 2026-04-27 — H-6 (part 2): replace SDK module-global `loop` with thread-local proxy

The first H-6 attempt (disable `auto_reconnect`, let outer loop own reconnects)
turned out to expose — not fix — a deeper bug. EC2 redeploy showed `narranexus-
lark` falling into a fast-reconnect loop: 10 minutes / 52 reconnects / 164
"attached to a different loop" errors / 0 inbound messages. The disconnect path
the SDK had previously been swallowing was actually being triggered on the
**first** connection of every thread.

### Real root cause: module-global `loop` is a cross-thread race

`lark_oapi/ws/client.py` defines `loop = asyncio.get_event_loop()` once at
import time on the main thread, then every `Client` method reads this same
module global on every use:

```python
loop.run_until_complete(self._connect())     # line 114
loop.create_task(self._ping_loop())          # line 126
loop.create_task(self._receive_message_loop())  # line 159
loop.create_task(self._handle_message(msg))  # line 171
```

The SDK is implicitly designed for a single Client per process. NarraNexus
runs N Clients concurrently in N daemon threads.

The previous M-9 patch was `with _WS_LOOP_PATCH_LOCK: ws_mod.loop =
fresh_loop` per thread. The lock only covered the assignment — not the
subsequent `ws_client.start()`. After thread A released the lock, thread B
could overwrite the global with `fresh_loop_B`. Thread A's `start()` then
read `loop` on every line and intermittently picked up thread B's loop. The
`_receive_message_loop` task ended up bound to a different loop than the
websocket future it awaited, producing
`RuntimeError: Task got Future <Future pending> attached to a different loop`.

This was reproduced cleanly with a 5-thread reproducer
(`/tmp/lark_loop_race_reproducer.py`) — 28/40 observations saw a foreign
thread's loop.

### Fix: install a thread-local proxy at module-import time

`asyncio.get_event_loop()` is already thread-local (it reads back whatever
`asyncio.set_event_loop()` stored on the calling thread). Replacing the SDK's
module global with a proxy whose `__getattr__` delegates to
`asyncio.get_event_loop()` makes every SDK call resolve to the calling
thread's loop — no shared mutable state, no race window.

The patch lives in `lark_trigger.py` at module-import scope:

```python
class _ThreadLocalLoopProxy:
    def __getattr__(self, name):
        return getattr(asyncio.get_event_loop(), name)
    def __bool__(self):
        return True
    def __repr__(self):
        ...

def _install_lark_oapi_loop_proxy():
    import lark_oapi.ws.client as _ws_client_mod
    if not isinstance(_ws_client_mod.loop, _ThreadLocalLoopProxy):
        _ws_client_mod.loop = _ThreadLocalLoopProxy()

_install_lark_oapi_loop_proxy()
```

`_subscribe_loop.run_ws()` is reduced to:

```python
fresh_loop = asyncio.new_event_loop()
asyncio.set_event_loop(fresh_loop)        # proxy reads this
ws_client._lock = asyncio.Lock()          # bind to fresh_loop
ws_client.start()                          # SDK now resolves loop per-thread
```

`_WS_LOOP_PATCH_LOCK` is removed — there is no longer any per-thread mutation
of SDK state to serialise.

### Why this is the right level to fix

- One module-import-time install, no per-thread bookkeeping.
- Survives SDK upgrades that add new `loop.<method>()` call sites: `__getattr__`
  proxies any new attribute automatically.
- `auto_reconnect=False` (added in part 1 of H-6) stays — the SDK's internal
  retry path is no longer load-bearing because the outer `_subscribe_loop`
  reconnect machinery (H-1 / H-5 / audit rows) now actually works through the
  whole chain. SDK reconnects had been the dominant failure mode precisely
  because they used the racing `loop` global without re-patching.

### Verification

- Reproducer pre-patch: 28/40 cross-thread misses (5 threads, 8 iterations).
- Reproducer post-patch (`/tmp/lark_loop_proxy_test.py`): 40/40 thread-local
  lookups + 40/40 method bindings correct.
- EC2 verification pending (apply this commit, redeploy, watch first 10
  minutes for `attached to a different loop` count → expect 0).

## 2026-04-27 — H-6 (part 1, superseded by part 2): disable SDK auto_reconnect, let outer loop own reconnects

EC2 production observation: the `narranexus-lark` container had been up
3 days, processed 0 inbound `im.message.receive_v1` events in the last
24 h, but the process was still RUNNING and the container `healthy`.
Logs showed 3 keepalive timeouts followed by 8 cascading
`RuntimeError: Task got Future <Future pending> attached to a different
loop` exceptions, all inside `lark_oapi/ws/client.py:170`.

Root cause: `lark.ws.Client` defaults to `auto_reconnect=True`. After a
keepalive timeout, the SDK's `_receive_message_loop` calls
`_disconnect()` then `_reconnect()` **internally**, in the same thread.
The thread's fresh asyncio loop (set up by `_subscribe_loop` and
documented in M-9) is still current, but the `_reconnect()` path inside
the SDK does NOT re-patch `lark_oapi.ws.client.loop` — and the new
connection ends up with futures bound to a different loop than the
`_receive_message_loop` task. The exception is caught and swallowed
inside the SDK, so `ws_client.start()` never returns, the daemon thread
stays alive forever, and the outer `while self.running` loop here never
gets a chance to restart the subscriber.

Fix: pass `auto_reconnect=False` to `lark.ws.Client(...)` in
`_subscribe_loop`. On disconnect the SDK now `raise`s instead of
swallowing — `ws_client.start()` returns, `run_ws` populates
`thread_error`, the polling loop sees `t.is_alive() == False`, the
existing `if thread_error: raise thread_error[0]` propagates to the
outer `except Exception`, and the existing backoff + restart machinery
takes over. This path was already designed for normal disconnects
(`H-1`, `H-5`, audit rows for `WS_DISCONNECTED` / `WS_BACKOFF`); it had
just never been exercised because the SDK's silent retry kept claiming
ownership of reconnects.

Implications:
- Reconnects now go through `LarkCredentialManager.get_credential` each
  iteration, so a re-bind / app-secret rotation no longer requires a
  process restart to take effect. Net positive over SDK auto-reconnect,
  which would have kept using the stale credential.
- Every disconnect now emits `EVENT_WS_DISCONNECTED` and (eventually)
  `EVENT_WS_BACKOFF` audit rows — the silent failure mode is gone.
- Reconnect backoff is now driven by `_compute_next_backoff` (5 s base,
  120 s cap). Slightly slower than the SDK's immediate retry, but the
  SDK retry was broken anyway.

## 2026-04-21 follow-up — enriched ingress logging ("who sent what to whom")

After another incident review where we needed to know "did an operator's
message actually reach the bot?" and the only audit evidence was
`event_type=ingress_processed` (no content, no sender info), we
expanded the ingress path so every incoming Lark message leaves a
scannable breadcrumb BEFORE the dedup decision fires.

- **`_dedup_and_enqueue` now emits a single loguru INFO line at entry**
  of the form `LarkTrigger ingress | agent=... app=... <- from=...
  chat=...(chat_type) msg_id=... type=... preview='...'`. This runs
  regardless of whether the message is later accepted, deduped, or
  dropped as historic replay, so operators can tell "the message
  arrived but was filtered" apart from "the message never arrived".
- **Audit rows for `ingress_processed` / `ingress_dropped_*` carry
  `message_type`, `chat_type`, and `content_preview` in `details`**,
  so the `lark_trigger_audit` table is itself enough to answer "what
  did the user actually send?" without scraping container logs.
- **New static helper `_preview_message_content(raw_content,
  message_type)`** knows the shape of Lark's per-type JSON payloads
  (text, post, file/image, and a generic fallback), pulls out the
  most-useful text field, collapses whitespace, and truncates to 160
  chars. Test coverage lives in
  `tests/lark_module/test_lark_audit_trail.py`.

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
either scenario — an operator observed the agent answering the same user
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
