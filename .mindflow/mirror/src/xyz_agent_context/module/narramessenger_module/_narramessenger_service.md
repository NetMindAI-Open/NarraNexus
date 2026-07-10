---
code_file: src/xyz_agent_context/module/narramessenger_module/_narramessenger_service.py
stub: false
last_verified: 2026-07-02
---

## 2026-07-02 (Commit 6) — bind flow switched to Direct Matrix

The driver now completes the bind by calling
`POST /bind-agent/runtime-ready?token=<bind_token>` (Direct Matrix) instead
of `POST /api/agent-gateway/connect` (Gateway/Polling). The setup guide's
Runtime Selection Rule is explicit that Direct Matrix is the default path
("Do not ask the creator to choose Matrix vs Gateway"). Gateway is still a
supported transport on the NarraMessenger backend for callers that hit
`/connect` directly with a stored bearer, but this driver no longer takes
that path.

Two new behaviours land with the switch:

1. `_parse_setup_guide` now extracts a fourth field — `matrix_access_token`,
   the `syt_...` string from the guide's Matrix Connection Details table.
   The Matrix HTTP API accepts ONLY this token; the guide is explicit that
   Matrix rejects the Narra bearer with `M_UNKNOWN_TOKEN`. The regex is
   conservative (`syt_` + 20+ alphanumerics) so stray `syt_short` prose
   examples don't shadow the real value.
2. `do_bind` refuses to persist a credential row when the guide reveals
   the bearer but not the Matrix access token. Reason: MatrixTrigger.connect
   raises ValueError on empty access_token, which the base treats as a
   permanent failure and disables the credential. Returning a clean error
   from `do_bind` gives the owner a better recovery hint ("re-copy the bind
   link and try again") than a silent auto-disable of a half-provisioned
   row.

The response's `roomId` is stored as `bind_room_id` on the credential — the
room where the owner will send the first test message per the guide's
Post-Bind Routing Verification step.

## Why it exists

The deterministic bind-flow driver, shared by the `narra_bind` MCP tool and the
`/api/narramessenger/bind` backend route. It replaces the original fragile path
(tell the agent to read `setup-guide.md` and self-bind) — where the agent could
pick Direct/Gateway on its own and often failed to persist the credential to our
DB. Here WE always pick Direct Matrix and always write the row.

## Design decisions

- **Single input = a pasted bind command/link.** `_parse_bind_command` extracts
  the `<token>` from a `.../<token>/setup-guide.md` URL (and derives the base URL
  from the host), or falls back to the last token-looking word for a looser
  `narra bind <token>` paste, defaulting the base to `api.netmind.chat`.
- **Flow**: GET setup-guide → if the bearer isn't revealed yet, `report-profile`
  (the agent's name+bio from the `agents` table, truncated to 30/200) → re-GET
  the guide → regex the bearer out of the `Authorization: Bearer <token>` line →
  `POST /api/agent-gateway/connect` with that bearer to activate Gateway + finish
  the bind → `upsert` the credential row.
- **Identity from the connect response, not the markdown** — `matrixUserId` /
  `principalId` / `roomId` come back from `/connect` (authoritative); only the
  bearer (+ homeserver, best-effort) are scraped from the rendered guide.
  **These three ids describe the AGENT's own Matrix identity, not the
  binder's** — `/connect` never returns who ran the bind flow. `roomId` is
  stored as `bind_room_id` and is the only trace of "where the bind
  happened"; it's what `NarramessengerTrigger._maybe_claim_owner` later
  matches against to auto-claim an owner (see that trigger's mirror doc).

## Gotchas

- **Bearer extraction is regex-over-markdown** (`_BEARER_RE`) — fragile if the
  platform changes the setup-guide layout, or if a placeholder `Bearer` appears
  before the real one. First match wins (Authentication section renders first).
- **The bind state machine may require human confirmation** (`creator_confirmed`)
  on the NarraMessenger side before the bearer is revealed. If so, `do_bind`
  returns a clear "not connected yet — confirm on NarraMessenger" error rather
  than guessing; the owner confirms there and re-pastes.
- WRONG_STATE from `report-profile` is treated as "already past the profile step"
  and ignored, so re-binding an already-connected session still works.
- **`do_bind` never writes `owner_matrix_user_id`/`owner_name`** (X2/X3 root
  cause, fixed 2026-07-02) — don't "fix" this here by trying to scrape an
  owner identity out of the setup-guide markdown; the guide describes the
  AGENT's binding, not the binder. Owner resolution happens downstream, on
  first inbound DM in the bind room (`NarramessengerTrigger._maybe_claim_owner`).
