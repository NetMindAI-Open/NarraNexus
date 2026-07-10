---
code_file: src/xyz_agent_context/module/narramessenger_module/matrix_trigger.py
stub: false
last_verified: 2026-07-09
---

## 2026-07-09 (review fixes) — full progress removal + inline

PR #78 review round. Four cleanups applied on top of the 2026-07-08
refactor:

1. **`_handle_stream_event` no longer has a `narra_progress` branch.**
   The tool itself was deleted in the same PR (see
   [[_narramessenger_mcp_tools]] 2026-07-09), and a stray call from an
   older prompt now falls through as an unknown tool call — silent,
   no state change. Test: `test_stray_narra_progress_is_inert`.
2. **`_finalize_stream_with_reply` inlined.** The method had degraded
   to a single `await self._send_matrix_reply(...)` call, and its
   `state` parameter was unused. Removed and inlined at the sole
   caller in `_build_and_run_agent_streaming`. Only
   `_finalize_stream_silent` remains as a distinct finalize hook.
3. **Test coverage extended to the outer try/except path.** The
   belt-and-suspenders branch where `async for event in client_stream`
   raises a Python exception (rather than emitting
   `MessageType.ERROR`) had no test — that's exactly the path
   `LineTooLong` from the anyio 128 KiB readline limit percolates up
   through. New test
   `test_run_stream_exception_surfaces_error_marker` asserts the
   room receives `STREAM_ERROR_MARKER` in that scenario.
4. **`narramessenger_context_builder`'s `reply_instruction` no longer
   mentions `narra_progress`.** Prompt now tells the agent explicitly
   that the room stays quiet until `narra_reply` fires (see the
   [[narramessenger_context_builder]] 2026-07-09 note).

## 2026-07-08 (UX refactor) — no placeholder, silent = quiet room

Dev feedback: even after the 2026-07-03 fix (edit-to-`·`-marker instead
of redact), users flagged the "💭 Thinking… → ·" sequence for casual
messages ("ok"/"嗯"/etc.) as visually confusing. The agent appears to
"try to say something" then trails off — worse UX than staying silent.

**Explored & rejected**: deferred/lazy placeholder (send only if agent
takes >800 ms). RDS timing data (2026-07-08) shows our typical Matrix
turn is 20–60 s; the ONE fast case in ~30 days of dev traffic was 5.4 s.
No reasonable delay window skips the placeholder on the silent path
without also delaying it on the reply path.

**Decision**: kill the placeholder outright. The room stays as-is while
the agent works; the trigger fresh-sends when there's something to say.

Rules matrix (updated after 2026-07-09 review fixes):

| Situation                        | Room outcome                       |
|----------------------------------|------------------------------------|
| Agent calls `narra_reply(text)`  | Fresh `room_send` on finalize      |
| Agent stays silent (no reply)    | NO-OP — room untouched             |
| Runtime crash (ERROR / raise)    | Fresh `room_send` `STREAM_ERROR_MARKER` — failures MUST stay visible |

Constants surviving: `STREAMING_ENABLED`, `STREAM_ERROR_MARKER`.
Removed: `STREAM_PLACEHOLDER_TEXT`, `STREAM_PROGRESS_MIN_INTERVAL_MS`,
`STREAM_SILENT_MARKER`. Removed method: `_apply_progress` (and after
2026-07-09, `_finalize_stream_with_reply` was inlined too). Removed
imports: `matrix_room_edit`, `matrix_room_redact` (the `m.replace` and
redaction paths no longer trigger).

`_StreamReplyState` slimmed down: `placeholder_event_id`,
`last_progress_ms`, `send_failure` gone. Only `narra_reply_text`,
`error_seen`, `last_error_message` remain — the trigger no longer needs
to track any Matrix event ids across the stream.

The 2026-07-09 review round finished the job on `narra_progress`: the
MCP tool was deleted entirely (not just neutered), and the prompt no
longer instructs the agent to call it.

Underlying `LineTooLong` on oversized `tool_call_output_item` still
requires an upstream fix (Claude Agent SDK / anyio 128 KiB readline
limit). Filed as an agent_framework follow-up.

## 2026-07-03 (UX fix) — silent finalize: edit-to-marker, not redact

**Superseded by the 2026-07-08 refactor above** — kept for provenance.


Live incident on dev EC2 (`agent_3dbc1343a078`, room
`!nkPGwwuXRnhUBvJOlC:matrix.netmind.chat`) surfaced two overlapping
bugs the redact path hid:

1. **`aiohttp.http_exceptions.LineTooLong: Got more than 131072 bytes`**
   fatal-crashed the agent turn on `tool_call_output_item` outputs
   >128 KiB (Claude Code's `Read` on a PNG returned base64 that
   exceeded anyio's hardcoded line limit). Every turn ended
   `_finalize_stream_silent → matrix_room_redact` → room showed
   "message deleted" as the ONLY visible outcome. The runtime
   fatal was in the log but never surfaced to the sender.
2. Even for INTENTIONALLY-silent turns (agent chose not to speak),
   `redact` renders in every Matrix client as a prominent "message
   deleted" line — misleading enough to look like a bug.

Fix: `_finalize_stream_silent` now EDITS the placeholder (via
`m.replace`) instead of redacting. Two new class-level constants:

- `STREAM_SILENT_MARKER = "·"` — discreet dot for the "no reply
  intended" case. Keeps a single message thread in the room without
  the alarming delete indicator.
- `STREAM_ERROR_MARKER = "⚠️ Sorry — I hit an internal error and
  couldn't finish. Please try again in a moment."` — used when the
  runtime emitted a `MessageType.ERROR` (or the stream loop caught
  an exception) during the turn. Tells the sender to retry rather
  than assume they were ignored.

`_StreamReplyState` gains two fields the state machine writes at ERROR
time:
- `error_seen: bool` — set by `_handle_stream_event` when
  `MessageType.ERROR` fires, and defensively by the outer
  `run_stream` try/except so a Python-level exception (like the SDK
  transport's LineTooLong percolating up) also triggers the error
  marker path.
- `last_error_message: str` — truncated excerpt logged at finalize
  time (`last_error=...`) so live debugging doesn't need to re-open
  the log for the same turn.

Finalise fallback chain when a placeholder exists:
1. Try `matrix_room_edit` → success is the happy path.
2. If edit fails transient → try `matrix_room_send` of the marker
   (fresh message) so the sender still sees something meaningful.
3. If BOTH fail → attempt `matrix_room_redact` as last-resort
   cleanup (better a redact than a stranded "💭 Thinking…" in the
   room).

No-placeholder cases:
- Error + no placeholder: fresh-send the error marker (user must
  see the failure).
- Silent + no placeholder: no-op (room is already clean).

Follow-up (out of scope for this trigger fix): the `LineTooLong` at
128 KiB is a Claude Agent SDK / anyio bug — the SDK transport uses
`TextReceiveStream` on the CLI's stdout with anyio's default
readline limit. Bumping requires a monkey-patch on the SDK's
subprocess transport or an upstream fix. Filed as a follow-up on
the agent_framework side; the trigger just surfaces the failure
gracefully now.

Tests: `test_matrix_streaming_reply.py` gains
`test_finalise_silent_edits_placeholder_to_silent_marker`,
`test_finalise_error_edits_placeholder_to_error_marker`,
`test_finalise_error_no_placeholder_sends_fresh_marker`,
`test_error_event_sets_error_seen_flag`. The old
`test_finalise_silent_redacts_placeholder` is replaced (the redact
was the bug).

## 2026-07-03 (review fixes) — sync-loop robustness + mention scoping

PR #60 review round. Four correctness/robustness fixes:

- **`client.sync()` now checks `isinstance(resp, SyncResponse)`.** nio returns
  a `SyncError` object (not an exception) on a Matrix-level failure — a revoked
  token used to be read as an empty-rooms success and 120s-reconnect forever,
  so `is_permanent_auth_failure → disable_credential` never fired. On non-success
  we `raise RuntimeError(f"...{status_code} {message}")`; the base classifies the
  errcode (M_UNKNOWN_TOKEN/… → disable; else → backoff).
- **`AsyncClientConfig.max_timeouts` 0 → 1.** `0` = nio retries transport errors
  internally forever and never returns, so the base's backoff/reconnect (and its
  per-subscriber transport audit — the L2 liveness signal) never ran → silent
  zombie (incident #1/#4). `1` surfaces the error so the base owns retry.
- **`_is_mentioning_us` surface (3) is now room-scoped + `@`-prefix only.** It
  walked `_display_name_cache` across ALL rooms and matched the name as a BARE
  word (`name in body.split()`), so "I called the Agent" false-fired
  group_mention → a full agent run + group reply. Now uses only
  `(this_room, my_id)`'s cached name and requires the explicit `@name` form;
  raw MXID (2) and MSC3952 (1) remain the other two legitimate surfaces.
- **Silent-debounce `create_task` got an `add_done_callback`** (`_on_flush_task_done`)
  — a crash in `_debounce_flush → _flush_silent` was only a GC "never retrieved"
  warning (incident #2); now logged as ERROR.
- Minor: `_apply_progress` uses `get_running_loop().time()`.

Tests: mention regressions in `test_matrix_classifier_and_cache.py` (bare-word +
cross-room now group_silent).

## 2026-07-03 (redesign) — streaming is now "thinking + agent-driven progress"

Owner feedback: the token-delta streaming (below) read as jumpy — Matrix
`m.replace` fully redraws the body per edit, so debounced token chunks flicker,
and for non-Claude models the raw `AGENT_RESPONSE` output isn't clean answer
text anyway. Replaced the whole delta-streaming machine with an intentional-only
model:

- **Ship a `💭 Thinking…` placeholder IMMEDIATELY** at turn start (one
  `room_send`), before consuming the stream — instant feedback.
- **`AGENT_RESPONSE` deltas + `AGENT_THINKING` are now IGNORED.** The room shows
  only what the agent deliberately posts. No accumulation, no debounce, no char
  gates — those constants/fields (`STREAM_MIN_CHARS_BEFORE_PLACEHOLDER`,
  `STREAM_EDIT_DEBOUNCE_MS`, `STREAM_EDIT_MIN_DELTA_CHARS`, `accumulated_text`,
  `last_edit_ms`, `last_edited_length`, `_maybe_ship_or_edit`) are GONE.
- **New `narra_progress(text)` tool** (marker in [[_narramessenger_mcp_tools]]):
  the agent optionally calls it during long work; `_apply_progress` `m.replace`-
  edits the placeholder to that status, rate-limited by
  `STREAM_PROGRESS_MIN_INTERVAL_MS` (900ms floor vs Matrix's ~1 msg/s budget).
  The prompt (`narramessenger_context_builder`) tells the agent it's optional.
- **`narra_reply(text)`** → final `m.replace` overwrites the placeholder with the
  answer (fresh `_send_matrix_reply` if the placeholder never shipped). Silent
  turn → redact. Finalize methods unchanged.

Net: one message that shows `💭 Thinking…` → (optional agent status lines) →
final answer, updating in place. `_StreamReplyState` shrank to
`placeholder_event_id / narra_reply_text / last_progress_ms / send_failure`.
Tests rewritten in `test_matrix_streaming_reply.py`. The token-delta description
below is SUPERSEDED — kept only for history.

## 2026-07-03 (hotfix) — streaming looked for tool calls on the wrong event type

Second live-run bug in the streaming state machine: every turn ended with
`_finalize_stream_silent` redacting the placeholder, so the user's room showed
"message deleted" AFTER a streamed answer that got wiped out at the end. The
agent was in fact calling `narra_reply` (verified in the log:
`Tool call: mcp__narramessenger_module__narra_reply` at 15:54:16), but our
handler wasn't recognising it.

Root cause: `_handle_stream_event` matched on `MessageType.TOOL_CALL` and read
`.tool_name` / `.tool_input`. In this codebase, tool calls are actually emitted
as `ProgressMessage` (`message_type=PROGRESS`) with `details.tool_name` +
`details.arguments`. The `AgentToolCall` schema class exists but is never
constructed — see the pattern in `run_collector.py:143-165` which we now
mirror. State never captured `narra_reply.text` → finalise silent → redact.

Same-turn secondary regression from that bug: the "streaming felt too fast /
jumpy" complaint. Matrix `m.replace` fully replaces the body per edit, so
each ship causes a full client-side redraw. With debounce 700ms + 30-char
delta, the client saw 2-3 flashes per second. Bumped defaults to
1200ms + 80 chars — fewer, larger updates read as OpenClaw-smooth.

Handler now recognises:
- `MessageType.PROGRESS` + `details.tool_name` matches `"narra_reply"`
- `details.arguments` as EITHER dict OR JSON-encoded string (some SDK
  paths serialise mid-stream)
- MCP-prefixed name (`mcp__narramessenger_module__narra_reply`) via
  substring match, not equality — locked in a test so a future
  refactor to strict equality regresses loudly.

Also added an INFO log at capture time so a future silent-redact regression
shows up in the log with a clean "captured narra_reply.text" line to search
for. If the log lacks that line but the redact fired, the mismatch is
between the event shape and the handler — same failure mode as this bug,
easier to diagnose.

## 2026-07-03 (hotfix) — streaming crashed on a wrong ChannelTag import

First live run after enabling streaming: the agent stopped responding to
NarraMessenger messages entirely. `_build_and_run_agent_streaming` lazy-imported
`ChannelTag` from `channel.channel_prompts` (where it does NOT live) → `ImportError`
on EVERY message, AFTER receive/classify, so it read as "not receiving". Fixed to
`from xyz_agent_context.schema.channel_tag import ChannelTag` (where the base and
Lark import it). Slipped through because the 12 streaming tests drive the state
machine sub-methods directly and never call `_build_and_run_agent_streaming`, so
its lazy import was never executed under test — a reminder that streaming needs a
live smoke, not just unit coverage. Event-field assumptions (`message_type` /
`delta` / `tool_name` / `tool_input`) were verified correct against
`schema/runtime_message.py` at the same time.

## 2026-07-03 (Phase 4) — progressive streaming via `m.replace`

New `_build_and_run_agent_streaming` path replaces the atomic single-
send with an OpenClaw-style live edit stream. Placeholder → debounced
edits from `AGENT_RESPONSE` deltas → final overwrite from
`narra_reply.text` when the tool call materialises. `AGENT_THINKING`
is intentionally ignored — the room shows the agent's final answer,
never its chain-of-thought.

Kill switch: `STREAMING_ENABLED = True` class-level constant. Flipping
to False routes through the preserved `_build_and_run_agent_atomic`
path (byte-identical to the pre-streaming behaviour) — used when
Matrix rate-limits get aggressive or when a debug session wants the
old shape.

State machine (see `_StreamReplyState`):

- **Placeholder gate**: no `room_send` until the agent has produced
  at least `STREAM_MIN_CHARS_BEFORE_PLACEHOLDER` characters. Prevents
  a "…" flash for turns the agent immediately silent-drops (which
  would then need a redact — extra event traffic + brief room
  pollution).
- **Edit debounce**: `STREAM_EDIT_DEBOUNCE_MS` + `STREAM_EDIT_MIN_DELTA_CHARS`
  must BOTH pass. Time alone lets a fast token stream ship a wall of
  1-char edits; delta alone lets a slow-generating agent ship 30
  edits per second the moment enough chars land. Both together
  keeps Matrix's per-room write budget happy.
- **Finalise with reply**: on `narra_reply(text=...)`, the accumulated
  streamed text is OVERWRITTEN by `text` via one last `m.replace`.
  This is the design's core safety property: any transient thinking
  we streamed is gone from the visible message by the time the turn
  ends. If no placeholder ever shipped (agent replied instantly),
  finalise falls back to `_send_matrix_reply` — retry-aware atomic
  send.
- **Finalise silent** (no `narra_reply`): redact the placeholder if
  one was sent; otherwise no-op. Room stays clean.

New HTTP surface in [[_matrix_send]]: `matrix_room_edit` (MSC2676
`m.replace` shape) and `matrix_room_redact` (Matrix's canonical
delete). Kept in the send module so future MCP tools can use them
too — they don't need trigger-instance state.

Behaviour vs Narra guide: OpenClaw's config sets `streaming: true`
under the assumption the runtime edits messages incrementally
(setup guide section 6b). NarraMessenger app already supports
message edits (Matrix native), so streamed edits render as one
message that updates in place. Cross-client compatibility: older
clients that ignore `m.replace` see BOTH the placeholder AND the
final edit as separate messages with a `* ` prefix on the edit —
mildly noisy but never broken.

Tests: `tests/narramessenger_module/test_matrix_streaming_reply.py`
(12 tests: placeholder gating, edit debounce, thinking ignored,
narra_reply capture / last-wins, finalise-with-reply edit path,
finalise-with-reply no-placeholder fallback, finalise-silent redact,
finalise-silent no-op, feature-flag off).

## 2026-07-03 (fix) — NarraMessenger "compound" multimodal, not inline m.image

Live test (agent_62cf67080ad4, 2026-07-03) showed the Phase-3 inline-media
path never fires for real NarraMessenger pictures — because NarraMessenger
does NOT send standard `m.image` events. A picture arrives as a **compound
message**: a plain `m.text` event whose custom
`content["ai.netmind.hint"]` = `{kind:"compound_trigger", compound_preview:
{text, media_url(mxc), mime_type, file_name}}`, plus a sibling
`msgtype:"ai.netmind.compound"` event (nio → RoomMessageUnknown) carrying
the raw bytes. Our code only read the `m.text` `body`, which is the *hidden*
`"[internal hint] process compound $…"` string (`ai.netmind.visibility:
hidden`) — so the agent got a cryptic reference, no image, and stayed
silent.

`_wrap_event` now detects `compound_trigger` and lifts the preview into a
`kind="m.room.message.compound"` raw dict: the REAL user text (not the
hidden body) + the mxc. `parse_event` turns that into a ParsedMessage whose
`content` is the user's text and whose `attachment_refs` points at the mxc —
so the **existing** `fetch_attachments` → `_download_mxc` →
`_persist_attachment` pipeline downloads it unchanged. The raw
`ai.netmind.compound` (RoomMessageUnknown) sibling is ignored — the preview
has everything, and NarraMessenger 403s our direct `/event` + `/messages`
reads, so the pushed /sync event's preview is our only handle.

An INFO log fires on each `compound_trigger` (media present + mime) so a
live run confirms the branch is reached. The old `m.room.message.media`
path is kept for any room that genuinely sends inline media.
Tests: `test_matrix_compound_ingest.py` (wire format captured verbatim from
the live event).

## 2026-07-03 (fix) — extract_output was reading the wrong raw_items shape

First live reply test: the agent DID call `narra_reply`, but the reply never
reached the room — logged "agent chose silent reply; nothing sent". Cause:
`extract_output` read `getattr(item, "details", …)`, but
`run_collector.collect_run` emits tool calls as **dicts**
`{"item": {"type":"tool_call_item","tool_name","arguments"}}` (the shape Lark's
extractor navigates). `getattr(dict, "details")` is always None → no match →
"" → dropped. The original send_message_to_user_directly version had the same
mismatch but was never exercised (prompt pointed at narra_send), so the bug
rode along until the reply path actually went through narra_reply. Now mirrors
Lark: `raw.get("item")` → check `type=="tool_call_item"` → `item.get("tool_name"/
"arguments")` (arguments may be a JSON string). The unit test now uses the real
shape, not a `.details` stand-in.

## 2026-07-03 — reply unified onto Matrix (extract_output reads `narra_reply`)

Sibling to the outbound-send unification (see [[_matrix_send]]). `extract_output`
now scrapes **`narra_reply`** (the agent's channel-reply marker, ``text`` arg),
NOT the generic ``send_message_to_user_directly`` — the shared channel prompt
(`channel_prompts.py`) reserves that generic tool for OWNER messages, so keying
the room reply on it was the bug behind "reply went out via Gateway `/chat/send`
while the room_send path idled and mislogged 'nothing sent'".

NarraMessenger stays **trigger-driven** on purpose (the agent calls `narra_reply`
as a marker; THIS trigger does the actual `room_send` in `_build_and_run_agent`
→ `_send_matrix_reply`). That's different from Lark/Slack/Telegram (where the
channel CLI tool sends and we only scrape) — owning delivery in the trigger is
the foundation for future progressive `m.replace` streaming (spec §7). Proactive
text (`narra_send`) and media (`narra_send_media`) are separate, immediate sends
via [[_matrix_send]].

## 2026-07-03 (Phase 3) — multimodal ingest (m.image / m.file / m.audio / m.video)

Promoted from the "Deferred to Phase 3" stub in the file docstring to a
working receive path. Diagnosed from a live report: a message carrying a
picture produced no agent reaction. Root cause was **not** a parse gap —
`_wrap_event` only recognised `RoomMessageText`, so every media event hit
the `else` branch and was dropped with a `skipping event type=…` debug
line **before** `parse_event` or any download could run. Three layers were
text-only; all three now handle media, mirroring
[[channel_trigger_base]]'s attachment contract that Lark / Slack /
Telegram / Discord already use.

Flow (one `RoomMessageMedia` — the shared base of Image/File/Audio/Video):

1. **`_wrap_event`** — `isinstance(event, RoomMessageMedia)` marshals the
   mxc URI (`event.url`), filename (`event.body`), and the `content.info`
   block (`mimetype` / `size`) from `event.source` into a
   `kind="m.room.message.media"` raw dict. mimetype is only a hint —
   `_persist_attachment` re-sniffs the real MIME from bytes.
2. **`parse_event`** — media kind → one `attachment_refs` entry
   (`mxc_url` / `original_name` / `mime_hint` / `size_hint`) + coarse
   `content_type` (msgtype first, MIME family fallback). `content=""`
   (the filename is the attachment's `original_name`, NOT message text),
   so a caption-less image still flows — the base guard passes because
   `attachment_refs` is non-empty. The raw dict is **copied** before
   adding refs so the base's dedup key (which still holds the original)
   is untouched.
3. **`fetch_attachments`** (override) — for each ref: parse
   `mxc://{server}/{media_id}`, size pre-check vs
   `backend_settings.max_upload_bytes`, `_download_mxc` → base
   `_persist_attachment` (workspace store + MIME sniff + audio STT →
   `Attachment`). Never raises; audits `EVENT_INGRESS_DROPPED_OVERSIZED`
   / `EVENT_ATTACHMENT_FETCH_FAILED` / `EVENT_ATTACHMENT_PERSISTED`.
   Structure is a near-copy of `DiscordTrigger.fetch_attachments`; the
   **only** channel-specific part is the download source.
4. **`_download_mxc`** — the single network seam (tests monkeypatch it).
   `GET {homeserver}/_matrix/client/v1/media/download/{server}/{media_id}`
   with `Authorization: Bearer {matrix_access_token}` (the **Matrix**
   token, not the Narra bearer). The legacy unauthenticated
   `/_matrix/media/r0/download` path is gone on `matrix.netmind.chat`
   (verified 2026-06-30). Streams with a `max_bytes` cap so a lying
   `size_hint` can't blow memory; over-cap mid-stream →
   `MatrixMediaError("oversized")`, which the caller audits as OVERSIZED
   (not FETCH_FAILED) via the `code` split — same idea as
   `DiscordSDKError.code`.

**Why the prompt "download location" is free (design note the owner
called out):** we deliberately route through `_persist_attachment`
instead of storing bytes ourselves. That helper generates the `att_`
file_id and writes under the WS-upload path, so
`Attachment.synthesize_marker` (ChatModule) later emits
`[User uploaded image: … path=<abs> … — use Read tool to view]` with a
path `resolve_attachment_path` can actually resolve. Storing to a
self-invented path/file_id would make the marker render `<unavailable>`
and the agent could not open the file. The test asserts this end-to-end
(marker path == `resolve_attachment_path` == on-disk bytes).

Sending images back (agent-generated) is still out of scope this version
(§8.2 of the spec). Encrypted media (`EncryptedEvent`) is still dropped —
rooms are plaintext by default; that remains the separate Phase 3+ item
noted below.

Tests: `tests/narramessenger_module/test_matrix_attachment_ingest.py`.

## 2026-07-02 (owner override) — SILENT_BYPASS_AUTHORIZE

Live E2E confirmed the diagnosis from the INFO-log hotfix: Narra's
`authorize-event` denies group events with `mentioned=False` by
policy (verified verbatim on `agent_62cf67080ad4` — every non-@
group message returned `allow=False` with no notice). The owner's
product intent is different: the agent has the right to hear a
group it's in, it just shouldn't reply unless addressed.

`_process_message` is reordered so classification happens FIRST,
then authorize-event runs only on paths where the agent will
actually reply / invoke tools / call the model. Specifically:

- `dm` / `group_mention` → authorize-event REQUIRED (unchanged;
  matches guide's intended domain).
- `group_silent` → authorize-event SKIPPED when the class-level
  constant `SILENT_BYPASS_AUTHORIZE = True`. The memory-only path
  proceeds regardless of Narra's decision.

`SILENT_BYPASS_AUTHORIZE` is a known-conflict-with-the-guide
constant, documented in the source and kept as a class field so it
can be flipped back to False in one line if NarraMessenger tightens
enforcement (in which case group non-@ collapses to Slack parity —
memory only when @-mentioned).

Renegotiation with the NarraMessenger team is in flight; the design
argument is that "listening to a room the agent has joined" is a
Matrix-native right, not a Narra-specific exception. When we hear
back, either the constant stays True (Narra agrees), or flips to
False and the design falls back to guide-strict.

## 2026-07-02 (post-Commit-7 hotfix) — diagnostic INFO + auto-disable broken creds

Two behavioural fixes surfaced during the first live E2E:

1. **Silent-path diagnostic logs promoted to INFO.** The
   `_process_message` decision points — client-missing drop, echo
   drop, authorize-event deny (with or without notice), classifier
   result — now log at INFO, and the silent-buffer lifecycle
   (`_enqueue_silent`, `_debounce_flush` fire/cancel, `_flush_silent`
   entry/exit, `_drain_all_silent_buffers`) is fully traced. Rationale:
   the first live test showed 0 `silent=True` runs on group non-@
   messages, and every silent-path failure mode was invisible at
   INFO. Now the log tells us exactly which branch a missing turn
   fell through.
2. **Broken credentials auto-disable on connect.** When
   `MatrixTrigger.connect` sees a `connection_mode='matrix'` row with
   an empty `matrix_access_token`, it now calls `disable_credential`
   BEFORE raising `ValueError`. Without this, the base's
   `_subscribe_loop` treated the ValueError as transient and retried
   every 120s forever, generating an ERROR + full stack trace on
   every retry. Pre-existing rows (pre-Matrix binds that never got a
   Matrix token) now flip `enabled=False` on first sight and the
   owner sees a single WARN telling them to re-bind, not a
   permanently noisy log stream.

## 2026-07-02 (Commit 7) — polling deleted; sole NarraMessenger trigger

`channel_name` reverts from `narramessenger_matrix` to `narramessenger`;
the `_matrix` suffix only existed to partition dedup / audit while the
polling `NarramessengerTrigger` coexisted. That trigger was deleted in
Commit 7, so the disambiguating suffix becomes clutter.

Practical consequence: any dedup rows written under
`channel_name='narramessenger_matrix'` become orphaned in
`channel_seen_messages`. Event-id uniqueness makes cross-partition
collisions impossible, so the orphans are just dead rows — cheap to
sweep via retention TTL, no data migration required.

`load_active_credentials()` no longer filters by `connection_mode`; it
calls the manager's `list_active()` and returns every enabled row.
Legacy Gateway rows without a `matrix_access_token` reach `connect()`,
raise `ValueError`, and get disabled by the base's watcher — the owner
is expected to re-run the bind flow to end up on Matrix.

The `_subscriber_key` still returns `matrix:<agent_id>`; provenance
documentation only, since no other trigger competes for the base's
subscriber map. Any consistent per-credential key works after
Commit 7 — no need to change it and lose the historical breadcrumb.

## 2026-07-02 (Commit 6) — auto-join invited rooms

The sync loop now walks `resp.rooms.invite` at the top of every batch
and calls `client.join(room_id)` for each invitee. Matches the setup
guide's OpenClaw config which sets `autoJoin: "always"`: the owner (or
any legitimate inviter) expects the agent to appear in the room without
a second confirmation step.

Failure handling: `client.join` errors are logged at WARNING and swallowed.
Rationale — the invite persists in `resp.rooms.invite` until the join
succeeds, so the next sync tick automatically retries. Propagating would
break the outer sync loop over a transient network blip and force a full
reconnect via the base's backoff path, which is a bigger hammer than
the failure warrants.

No dedicated audit event: auto-join is expected behaviour, not an
anomaly. If join throughput ever becomes ops-visible (e.g. mass
invites causing rate-limit trouble), promote to an audit line then.

## 2026-07-02 (Commit 5) — Narra authorize-event gate

Every Matrix event must clear
`POST {backend_base_url}/api/agent-runtime/matrix/authorize-event`
BEFORE we read history, write memory, invoke tools, call the model,
or send a Matrix reply. Applies to **both** silent and full paths —
silent still writes chat_history + observations, which the guide
explicitly lists as gated operations.

Implementation (`_authorize_event` + `_send_matrix_notice`):

- Uses the **Narra agent secret token** (`credential.bearer_token`), NOT
  the Matrix access token. These are different secrets with
  different scopes; the Matrix access token would return `401` from
  this endpoint.
- Fails **closed** on: non-2xx status, transport exception / timeout,
  invalid JSON body, `allow` field missing or not exactly `True`, or
  credentials missing `bearer_token` / `backend_base_url`. All → drop
  the event without further processing.
- 401 during pending bind is expected fail-closed behaviour (owner
  has not called `runtime-ready` yet). Logged at INFO, not WARNING,
  so it does not spam ERROR during the normal bind-in-progress
  window. It is NOT wired into `is_permanent_auth_failure` — that
  method only catches Matrix `M_*` codes, so a Narra 401 correctly
  will not disable the Matrix credential.
- On `allow != true` with `notice.send == true`, we forward exactly
  `notice.text` back to the same room as `m.notice`. This is the only
  side effect allowed for a denied event; no memory write, no tool
  call, no model call, no other reply. If `notice.send != true`, we
  drop silently.

Mention detection was split out of `_classify` into
`_is_mentioning_us` (Commit 5) so the gate can compute the
`mentioned` payload flag BEFORE classification runs, and `_classify`
reuses the value via a `mentioned=` kwarg — no double body scan.

## Why it exists

Phase 1 replacement for the polling long-poll on the NarraMessenger
message plane. NarraMessenger's backend runs a Matrix homeserver
(`matrix.netmind.chat`); this trigger is a `matrix-nio` client that
opens `/sync`, receives events causally, and posts replies back via
`room_send`. We host **zero** Matrix infrastructure — homeserver,
TLS, federation, storage, backups all belong to the NarraMessenger
team. The trigger runs in-process next to
[[narramessenger_trigger]] (polling), each subclass filters
credentials by `connection_mode` so both transports can coexist on
the same agent set during migration.

## Design decisions

- **Distinct `channel_name`**. `narramessenger_matrix` partitions the
  base's dedup store, audit log, and per-channel stats from the
  legacy polling trigger. Matrix `event_id` and NarraMessenger
  `invocation_id` live in different ID spaces and must not collide
  in one dedup bucket.
- **`working_source` stays `NARRAMESSENGER`**. Downstream of
  `parse_event` (context builder, module coordination, reply routing)
  treats Matrix and polling as the same product surface — the
  transport swap is invisible to Modules.
- **Non-`[e2e]` matrix-nio**. `matrix.netmind.chat` rooms are
  plaintext by default (verified empirically 2026-07-02 — new rooms
  have no `m.room.encryption` state event). Skipping libolm removes a
  native-C dependency and shaves ~30 MB from the runtime footprint.
  If NM ever flips a room to encrypted, `client.sync` will surface
  `EncryptedEvent` instances that we currently drop in `_wrap_event`
  — the failure mode is "agent stops seeing messages in that room",
  not "agent crashes". Phase 3+ concern.
- **Silent-batch buffering for group non-@ messages**. Group messages
  that don't mention the agent should still land in memory
  (chat_history, observations, entity descriptions) but MUST NOT run
  the agent LLM — that's the whole point of the memory-only path
  (see [[agent_runtime.py]] `silent=True` and
  [[channel_trigger_base]] `_build_and_run_agent_silent_batch`).
  Per-room debounce (5s idle) coalesces bursts into one silent-batch
  runtime call; the burst cap (20 msgs) forces flush on active
  channels so memory lag stays bounded.
- **Reply via `client.room_send`**. Unlike polling, there is NO 15-
  minute response-timeout ceiling — Matrix decouples message delivery
  from a synchronous request/response window. The agent can run for
  hours; when it finishes, one HTTP POST hands the reply to the
  homeserver, which fans out to every joined client (including the
  user's mobile app opened days later).
- **`extract_output` reads `send_message_to_user_directly`**. Matches
  the discipline of Lark / Slack / Telegram: only content the agent
  explicitly passed to the reply tool ends up in the room. Never
  falls back to `result.output_text` — that's the agent's internal
  reasoning stream, and posting it as a reply would leak
  chain-of-thought into a user-visible channel.

## The three-way classification

`_classify(client, message, credential)` returns one of:

- `"dm"` — 2 members in the room. Full agent-loop path via
  `_process_message` → `_build_and_run_agent` → reply.
- `"group_mention"` — 3+ members AND agent explicitly mentioned. Same
  as DM. Mention detection covers (1) MSC3952 intentional mentions
  (`m.mentions.user_ids`), (2) raw MXID inline, and (3) `@displayname`
  inline — the three surfaces different clients emit.
- `"group_silent"` — 3+ members AND no mention. Route to per-(agent,
  room) buffer, no agent LLM invocation. Flushed via debounce OR
  burst-cap OR reconnect-burst drain OR `stop()` drain.

## Cursor-save ordering (crash-safe restart)

The sync loop follows a strict ordering:

```
resp = await client.sync(since=X, timeout=30s)
for room in resp.rooms.join:
    apply member state events        # populate caches BEFORE classification
    for message event in timeline:
        yield event                    # base awaits _dedup_and_handle inline
if self._silent_buffer:
    await drain_all_silent_buffers    # backlog flush BEFORE cursor commit
update_since_token(next_batch)         # ONLY after all writes durable
```

If the process dies mid-batch, the cursor stays at `X`, the server
replays the same batch on next connect, and the base's event_id
dedup filters the already-handled messages. Persisting the cursor
before drain would lose the silent memory writes.

## Reply send retry policy

`_send_matrix_reply` handles the three real failure modes:

- `M_LIMIT_EXCEEDED` → honor `retry_after_ms`, retry. Not counted
  against `SEND_MAX_ATTEMPTS`.
- `M_UNKNOWN_TOKEN` / `M_MISSING_TOKEN` / `M_FORBIDDEN` → give up on
  the FIRST hit; the base's sync loop will trip
  `is_permanent_auth_failure` on its next `/sync` call and disable
  the credential. Do not burn 3 attempts against a dead token.
- Transient (network / 5xx / unknown status) → exponential backoff,
  cap `SEND_MAX_ATTEMPTS`.

Any failure that exhausts the retry budget writes an audit row of
type `transport_send_failed` (added to
`channel_audit_events.py` in the same commit) with `error_code`,
`attempts`, and a truncated `body_preview`. That's the only signal
owner-notification tooling can key on, so it MUST be written even
for permanent auth failures.

## What silent path DOES NOT send

The silent-batch runtime call fabricates an empty `PathExecutionResult`
(see [[agent_runtime.py]] silent-mode note), so `_build_and_run_agent`
is not on the path at all — no `_send_matrix_reply` call ever fires
for silent messages. The room never sees a stray "still thinking…"
or empty message from the agent while we're silently ingesting a
group thread.

## Related

- [[channel_trigger_base]] — base class; `_build_and_run_agent_silent_batch`
  is the shared silent-mode entry point.
- [[agent_runtime.py]] — `silent=True` runtime mode.
- [[chat_module.py]] — silent-batch write path in `hook_persist_turn`.
- [[narramessenger_trigger]] — polling transport, coexists with this.
- [[_narramessenger_credential_manager]] — where matrix creds live
  (`connection_mode='matrix'` rows).
