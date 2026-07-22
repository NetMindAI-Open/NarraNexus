---
code_file: src/xyz_agent_context/module/narramessenger_module/_narramessenger_mcp_tools.py
stub: false
last_verified: 2026-07-20
---

## 2026-07-21 — `narra_cli` docstring said "read-only"; agent refused to publish

Dev incident: asked to `explore publish`, an agent refused with "narra_cli only
supports query/context/status reads" — because the tool docstring **literally
said that** ("Run a narra-cli command for query/context/status ops. Use this to
READ … NOT to send", no `explore` in the command list). The MCP tool docstring
IS the description the LLM sees, so it outweighed the `_CLI_CAPABILITY` system
prompt that DID mention explore. Rewrote the docstring: narra-cli is a ready,
installed tool (don't reason about installing it / npm / tokens); it covers
explore **writes** (publish/list/delete) plus rooms/history/attachments/speech/
status; only CHAT messages go via `narra_reply`/`narra_send`. Companion tweak in
[[narramessenger_module]]'s `_CLI_CAPABILITY` ("don't refuse a write as
read-only"). (Root cause A of the same incident — narra-cli binary missing from
the MCP image — is a deploy rebuild, not code.)

## 2026-07-21 — `narra_guide` serves a curated static reference (no live-fetch)

`narra_guide` no longer live-fetches narra's `runtime.md` (which told agents to
install/configure narra-cli themselves → sandbox chmod failure). It returns
`get_guide()` — OUR curated, platform-adapted reference. No credential / network.
See [[_narra_guide]] 2026-07-21.

## 2026-07-20 (review) — orphan client deleted; single validate pass

- Removing `narra_status` left `_narramessenger_client.py` (`NarramessengerClient`)
  with **zero importers** (`do_bind` uses aiohttp directly) — the source file and
  its mirror md were deleted (铁律 #2/#8); its markdown-fetch overlapped the new
  [[_narra_guide]] anyway.
- `narra_cli` no longer calls `validate_command` then `sanitize_command`
  (double validation). It calls `sanitize_command` only — which validates AND
  parses in one pass and raises `ValueError` on a blocked/unparseable command
  (surfaced as `error: "invalid_command"`, reason in `message`).

## 2026-07-20 — `narra_cli` passthrough + `narra_guide`; `narra_status` / `narra_room_members` removed

Shifted the query/context surface from hand-wrapped Matrix-direct tools to a
narra-cli passthrough (design + impl specs in the Obsidian `Narramessenger 接入`
vault, 2026-07-20). Motivation: narra ships its own CLI that keeps growing;
wrapping each new command as an MCP tool is not sustainable. Lark's model
(one passthrough + whitelist + doc-on-demand) is the reference.

- **Added `narra_cli(agent_id, command)`** — validates via
  [[_narra_command_security]] (domain whitelist, `--token*` blocked, `im send`
  blocked transitionally), then runs through [[narra_cli_client]] (ephemeral
  token-file injection, CWD=agent workspace). Covers `room` / `im messages` /
  `im attachments` / `speech` / `status`.
- **Added `narra_guide(agent_id)`** — serves the live narra-cli reference from
  `{backend_base_url}/api/agent-guide/narra-runtime.md` (see [[_narra_guide]]).
- **Removed `narra_status`** → `narra_cli("status")`.
- **Removed `narra_room_members`** (was raw Matrix `/joined_members` via
  matrix_access_token) → `narra_cli("room info --room-id <id> --members")`
  (bearer/proxy). Behaviour delta (source + field shape) flagged for the dev
  e2e; nulls out the last direct use of `import aiohttp` +
  `NarramessengerClient` in this file (both imports dropped).

**Transport split (transitional, owner decision 2026-07-20):** SEND stays
Matrix-native — `narra_reply` (marker) / `narra_send` / `narra_send_media`
kept unchanged, because the proxy media path (moderation / `ai.netmind.compound`
/ failure modes) is not yet validated. That is also why `im send` is blocked in
`narra_cli`. Once proxy send/media is verified on dev, remove the dedicated send
tools AND the `im send` block together.

## 2026-07-09 — `narra_progress` tool removed entirely

Review of PR #78 flagged the intermediate "keep the tool as a stable
surface for future analytics" story as YAGNI — no consumer exists, and
leaving a tool that promises status updates but delivers none tempts
agents to call it (burning a tool round-trip) or to say "I've told you
I'm still working" in later replies (they haven't, from the sender's
point of view). Per project rule #2 ("no backwards compat"), the tool
is deleted outright.

Now removed from three places at once:

1. `_narramessenger_mcp_tools.py` — the `@mcp.tool()` registration and
   its docstring gone.
2. The file-header "Tools exposed" list — the bullet is gone; the
   `narra_reply` bullet gained a sentence noting that the previous
   `narra_progress` status-update surface no longer exists.
3. `matrix_trigger.py`'s `_handle_stream_event` — the
   `elif "narra_progress" in tool_name` branch is gone. A stray tool
   call with that name from an older prompt is silently dropped
   (unknown tool → no state change, no room activity), tested by
   `test_stray_narra_progress_is_inert`.

Companion prompt cleanup: [[narramessenger_context_builder]]'s
`reply_instruction` used to instruct the agent to "MAY call
`narra_progress` first with a few-word status". That whole clause is
gone in the same PR.

## 2026-07-03 — `narra_progress` marker (streaming progress) [SUPERSEDED]

**Superseded by the 2026-07-09 delete above** — kept for provenance.

## 2026-07-03 — send tools unified onto Matrix (+ media send)

The agent-facing send surface was reworked so ALL outbound is Matrix-native
(see [[_matrix_send]]); the Gateway `/chat/send` + `/invocations/{id}/reply`
paths are gone.

- **`narra_reply(agent_id, text)`** — now a **marker** (no invocation_id, no
  HTTP). It just validates + returns `{ok}`; the reply text rides in the tool
  call's arguments, which the trigger reads (`extract_output`) and delivers via
  `room_send`. This is the reply path.
- **`narra_send(agent_id, room_id, text)`** — repointed from `/chat/send` to
  Matrix `room_send` (proactive text; e.g. a Job push).
- **`narra_send_media(agent_id, room_id, file_path, caption?)`** — NEW. Uploads
  a workspace file to the homeserver media repo + `room_send`s an
  `m.image`/`m.file`/… event. Path-confined to the agent's own workspace
  (`resolve_workspace_file`), size-capped at `backend.max_upload_bytes`.
- `narra_status` / `narra_bind` / `narra_room_members` unchanged.

Why `narra_reply` is a marker (trigger delivers) while `narra_send` /
`narra_send_media` send immediately: a *reply* is delivered by the trigger so it
can later be streamed progressively (`m.replace`); a *proactive*/media send has
no triggering turn to attach to, so it sends now.

## 2026-07-02 — `narra_room_members` tool added

New MCP tool: `narra_room_members(agent_id, room_id)`. Live GET to
`{homeserver}/_matrix/client/v3/rooms/{room_id}/joined_members` using
the credential's **Matrix access token** (NOT the Narra bearer —
Matrix rejects the Narra bearer with `M_UNKNOWN_TOKEN`).

Why a tool, not prompt injection. The `ChannelContextBuilderBase`
protocol lets each channel populate a `room_members_section` that
would be auto-injected into every turn's prompt. Every existing
channel (Lark/Slack/Telegram/Discord/NarraMessenger) returns `[]`
from `get_room_members()` — the group-member roster is not
prompt-injected on any channel. Reason: for group channels of any
non-trivial size (10+ members), pasting the full roster on every
turn is a large recurring token cost for context the agent rarely
needs. Keeping it as a tool means the agent pays for the roster
only in the specific turns that need "who's here" awareness (@ a
specific person, "who's in this room?", route a message to a
subset).

Response shape: `{"ok": True, "members": [{user_id, display_name,
avatar_url}, ...], "count": N}` on success, `{"ok": False, "error":
<errcode>, "message": <details>}` on failure. Failures surface the
Matrix errcode (`M_UNKNOWN_TOKEN`, `M_FORBIDDEN`, ...) verbatim so
the agent can distinguish "wrong token" from "wrong room" from "not
in the room".


## Why it exists

The agent-callable MCP tools for NarraMessenger. Registered on the module's
FastMCP server (port 7833) via `register_narramessenger_mcp_tools`.

## Tools

- **`narra_reply(agent_id, invocation_id, text)`** — the REPLY path for an
  inbound message. Calls `/invocations/{id}/reply`, which delivers AND closes
  the invocation (so the platform does NOT fire the 15-min timeout). The
  `invocation_id` comes from the turn's identity block (threaded by
  `build_extra_data` from the poll payload).
- **`narra_send(agent_id, room_id, text)`** — PROACTIVE send via `/chat/send`
  (no invocation to close, no time limit). For agent-initiated messages (Jobs,
  follow-ups) — NOT for replying to a message you were invoked on.
- **`narra_bind(agent_id, bind_command)`** — bind from a pasted bind link;
  delegates to `_narramessenger_service.do_bind` (Gateway bind + write row).
- **`narra_status(agent_id)`** — sanitised binding view (no token) + live
  `/status` check.

## Design decisions

- Credential is fetched via `XYZBaseModule.get_mcp_db_client()` (the dev MCP
  server is multi-tenant; agent_id is a tool argument, not verified here —
  same model as telegram/slack).
- **reply vs send split (2026-06-18)**: replies MUST close the invocation, so
  they go through `narra_reply`→`/reply`; proactive messages have no invocation
  and go through `narra_send`→`/chat/send`. Using `/chat/send` for a reply was
  the cause of the 15-min "agent did not reply" timeout.

## Gotchas

- Tool names are load-bearing: the trigger's `extract_output` and the
  MessageSourceRegistry handler both match on `"narra_reply"` / `"narra_send"`.
  Renaming requires updating both.
