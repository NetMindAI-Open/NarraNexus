# Manyfold-facing API — NarraNexus container

The protocol Manyfold (and any future platform integrating NarraNexus
runtimes the same way) speaks to the container. Authoritative reference
for everything under the `/manyfold/*` namespace plus the OpenAI-compat
`/v1/chat/completions` endpoint.

This API is **only registered when the container boots with
`ENABLE_MANYFOLD_API=1`**. Vanilla NarraNexus deployments (cloud at
`agent.narra.nexus`, `bash run.sh` dev mode, the Tauri dmg) leave this
unset and the entire `/manyfold/*` + `/v1/*` surface returns 404 — they
keep using the original `/api/*` endpoints + WebSocket transport.

---

## Table of contents

1. [Auth model](#1-auth-model)
2. [Error envelope](#2-error-envelope)
3. [`GET /healthz`](#3-get-healthz)
4. [`GET /manyfold/diagnostics`](#4-get-manyfolddiagnostics)
5. [`GET /manyfold/agents`](#5-get-manyfoldagents)
6. [`POST /manyfold/agents`](#6-post-manyfoldagents)
7. [`PATCH /manyfold/agents/{agent_id}`](#7-patch-manyfoldagentsagent_id)
8. [`DELETE /manyfold/agents/{agent_id}`](#8-delete-manyfoldagentsagent_id)
9. [`GET /manyfold/agents/{agent_id}/files/roots`](#9-get-manyfoldagentsagent_idfilesroots)
10. [`GET /manyfold/agents/{agent_id}/files/list`](#10-get-manyfoldagentsagent_idfileslist)
11. [`GET /manyfold/agents/{agent_id}/files/stat`](#11-get-manyfoldagentsagent_idfilesstat)
12. [`GET /manyfold/agents/{agent_id}/files/read`](#12-get-manyfoldagentsagent_idfilesread)
13. [`POST /v1/chat/completions`](#13-post-v1chatcompletions)
14. [Fragment-auth URL pattern (Open Native UI)](#14-fragment-auth-url-pattern-open-native-ui)
15. [Identity & user mapping](#15-identity--user-mapping)
16. [Idempotency, ordering, & failure semantics](#16-idempotency-ordering--failure-semantics)

---

## 1. Auth model

Every `/manyfold/*` + `/v1/*` endpoint requires:

```
Authorization: Bearer <MANYFOLD_GATEWAY_TOKEN>
```

The token is a shared secret minted by the Manyfold platform when it
provisions a runtime. NarraNexus reads it from the `MANYFOLD_GATEWAY_TOKEN`
env var at boot and compares byte-for-byte on each request. There is no
JWT / Clerk / OIDC path on this surface.

### Identity propagation

Beyond the gateway-token check, two HTTP headers carry per-call identity:

| Header | Purpose | Required for |
|---|---|---|
| `X-User-Id` | NarraNexus-side user id this call acts as. Must be `mf_<...>` or `local-*`. If `mf_<...>` is unknown, NarraNexus auto-creates the row. | `/api/*`, `/ws/*` (when called through the same bearer) |
| (none) | `/manyfold/*` endpoints derive identity from the resource (`agent_id` → `agents.created_by`) so no per-call header is needed | `/manyfold/*` |

When the gateway token is present **and** the request hits a generic
`/api/*` or `/ws/*` endpoint (e.g. the native UI loaded with the
fragment-auth pattern), the auth middleware:

1. Trusts the gateway-token check completes.
2. Reads `X-User-Id` to determine identity.
3. If `X-User-Id` starts with `mf_`, idempotently `INSERT … ON CONFLICT
   DO NOTHING` the user row on first contact (so a freshly-deep-linked
   Manyfold user that never POSTed an agent still gets to use the UI).
4. Falls back to the first `users` row when `X-User-Id` is absent
   (legacy compatibility, single-user containers).

### Failure codes

| Code | Meaning |
|---|---|
| 401 missing/invalid `Authorization` | The token doesn't match the env var, OR the env var is unset and someone hit `/manyfold/*` anyway. |
| 401 missing X-User-Id | Only `/api/*` + `/ws/*` paths; happens when the bearer authed but no usable identity was passed and there's no default user row. |
| 404 path not registered | The container was booted **without** `ENABLE_MANYFOLD_API=1`. Same shape as any unknown route. |

---

## 2. Error envelope

`/manyfold/*` and `/v1/*` follow FastAPI's standard `HTTPException` shape:

```json
{ "detail": "agent 'agt-xxx' not found" }
```

`/v1/chat/completions` (OpenAI-compat) instead returns the OpenAI error
shape so existing OpenAI SDKs interpret it correctly:

```json
{
    "error": {
        "message": "agent not found",
        "type": "invalid_request_error",
        "code": "agent_not_found"
    }
}
```

---

## 3. `GET /healthz`

The one endpoint that does **not** require the gateway token. Used by
Kubernetes readiness probes and by Manyfold's reachability check.

```
GET /healthz
→ 200  {"status": "ok"}
```

Always returns 200 once the FastAPI app is up and `sqlite_proxy` has
booted. Does not touch the DB.

---

## 4. `GET /manyfold/diagnostics`

Single-shot self-check for runtime introspection. The platform can call
this before considering a container "ready for traffic" to surface
config drift early (missing Claude credentials, broken DB, read-only
data dir, …).

### Response

```json
{
    "image_version": "v1.0.0-dev",
    "manyfold_api_enabled": true,
    "checks": {
        "claude_cli_installed": true,
        "claude_credentials_configured": true,
        "frontend_dist_present": true,
        "gateway_token_set": true,
        "writable_data_dir": true,
        "writable_claude_dir": true,
        "db_reachable": true
    },
    "warnings": [],
    "all_ok": true
}
```

`warnings` lists `"check failed: <key>"` for every false check.
`all_ok = warnings.length === 0` for convenience.

---

## 5. `GET /manyfold/agents`

List **every** agent in the container, cross-user. Used by Manyfold's
reconcile loop to discover NarraNexus-side agents that weren't created
via Manyfold (auto-discovery path).

### Response

```json
{
    "object": "list",
    "data": [
        {
            "agent_id": "agt-agpgg...",
            "name": "test-001",
            "description": "",
            "agent_type": "general",
            "created_by": "mf_user_local_admin",
            "created_at": "2026-05-26T06:55:40+00:00",
            "is_public": false
        }
    ]
}
```

`agent_id` is the canonical join key between the two sides — Manyfold's
`agents.internal_id` always equals this value.

### Notes

- Returns the full table on every call (no pagination). Manyfold's
  reconciler runs at most every 30 s so the unbounded list is OK for
  the foreseeable agent count.
- Fields a Manyfold reviewer might expect but **doesn't** get:
  - `user_providers` / model configuration (lives in
    `user_providers` table, never surfaced cross-system)
  - `narratives` count / token usage (use the diagnostics endpoint if
    you need health rollups)

---

## 6. `POST /manyfold/agents`

Idempotent agent **+ user** provisioning. Called by Manyfold's adapter
when a user clicks "Add agent to runtime" in the Manyfold UI.

### Request body

```json
{
    "agent_id": "agt-agpgg...",
    "agent_name": "test-001",
    "description": "optional",
    "manyfold_user_id": "user_local_admin",
    "manyfold_user_email": "dev@local.test",
    "display_name": null,
    "inherit_provider_from": "bin"
}
```

| Field | Required | Effect |
|---|---|---|
| `agent_id` | ✓ | Becomes both the row's primary key AND Manyfold's `internal_id`. Caller chooses the value (Manyfold uses `agt_<base32>`). |
| `manyfold_user_id` | ✓ | Manyfold-side user id (without `mf_` prefix). Normalised to `mf_<sanitised>` on this side. |
| `agent_name` | optional | Falls back to `agent_id`. |
| `description` | optional | Stored in `agents.agent_description`. |
| `manyfold_user_email` / `display_name` | optional | Stored on the auto-created user row for display only. |
| `inherit_provider_from` | optional | NarraNexus user_id (e.g. `"bin"`) whose `user_providers` + slot bindings get cloned into the newly-created `mf_<...>` user. Skipped on idempotent re-runs (user already had providers). |

### Response

```json
{
    "agent_id": "agt-agpgg...",
    "user_id": "mf_user_local_admin",
    "user_created": true,
    "agent_created": true
}
```

`user_created` / `agent_created` are `false` on idempotent re-runs.

### Side effects (in order)

1. UPSERT into `users` (created_by = `mf_<id>`).
2. If `user_created` AND `inherit_provider_from` set: clone source
   user's `user_providers` rows + slot bindings into the new user.
3. UPSERT into `agents` (created_by points at the new/existing user).

### Errors

| Status | Cause |
|---|---|
| 400 | `manyfold_user_id` normalises to an empty string (after stripping non-alphanumerics). |
| 401 | Auth failed (see §1). |
| 500 | Provider clone partially failed — logged as warning, agent row still committed. Manyfold should surface this so the operator can re-run with the right `inherit_provider_from`. |

---

## 7. `PATCH /manyfold/agents/{agent_id}`

Manyfold-initiated rename / description edit. Used by the bidirectional
sync (Plan A — push) so Manyfold can keep NarraNexus's row in lockstep
when a user renames an agent in the Manyfold UI.

### Request body

Every field is optional. Absent fields leave the existing value alone.
Empty string is honored for `agent_description` (intentional clear);
use `null` / omit to skip.

```json
{
    "agent_name": "renamed",
    "agent_description": "new description"
}
```

| Field | Constraint |
|---|---|
| `agent_name` | 1–200 chars when present. |
| `agent_description` | ≤2000 chars; empty string clears the field. |

### Response

```json
{
    "agent_id": "agt-agpgg...",
    "name": "renamed",
    "description": "new description",
    "updated_fields": ["agent_name", "agent_description"]
}
```

`updated_fields` lists which DB columns actually changed. An empty
patch (no fields, or all unchanged values) returns the row unchanged
with `updated_fields: []` — explicit no-op rather than an error so
Manyfold can call this unconditionally on every PATCH without
short-circuiting.

### Errors

| Status | Cause |
|---|---|
| 404 | `agent_id` doesn't exist. Manyfold treats this as "agent vanished on NarraNexus side; abort the user-facing rename and surface the error". |
| 422 | Field validation (length, type). |
| 401 | Auth failed. |

### Side effects

A single `UPDATE agents SET … WHERE agent_id = …`. No cascading to
narratives, events, instances, or other tables — only the metadata
columns change.

---

## 8. `DELETE /manyfold/agents/{agent_id}`

Cascade-delete. Removes the agent row **and** every derived row keyed
by `agent_id` across ~17 tables (events, narratives, module_instances,
IM channel credentials, message bus state, artifacts, instance_jobs,
cost_records, team_members, …).

### Response

```json
{
    "deleted": true,
    "agent_id": "agt-agpgg...",
    "cascade": {
        "events": 6,
        "narratives": 3,
        "module_instances": 12,
        "instance_jobs": 0,
        "agent_messages": 8,
        "...": "..."
    }
}
```

`cascade` reports rows removed per table — useful for the platform's
audit log.

### Errors

| Status | Cause |
|---|---|
| 404 | Agent already gone. Caller should treat as success (idempotent delete). |
| 500 | One of the cascade tables raised. We commit what we can and surface the partial result — manual cleanup may be needed. |
| 401 | Auth failed. |

---

## 9. `GET /manyfold/agents/{agent_id}/files/roots`

Read-only file tree — `roots` endpoint. Returns the set of "browsable
roots" the agent exposes. NarraNexus today exposes exactly one root,
the per-(agent, user) workspace directory.

### Response

```json
{
    "roots": [
        {
            "id": "workspace",
            "label": "Workspace",
            "path": "/data/workspaces/agt-agpgg..._mf_user_local_admin",
            "writable": false,
            "supportsListing": true
        }
    ]
}
```

`writable: false` is permanent — the Manyfold-side client refuses
write operations (write / mkdir / mv / rm) at the client layer too. If
you need to write into an agent's workspace, do it through one of the
agent's own tools (which writes via the same FastAPI app holding the
right user identity).

---

## 10. `GET /manyfold/agents/{agent_id}/files/list`

List immediate children of a directory.

### Query parameters

| Param | Required | Notes |
|---|---|---|
| `path` | optional | Absolute path. Empty string / `/` / root path itself = list the workspace root. |

### Response

```json
{
    "entries": [
        {
            "name": "Bootstrap.md",
            "type": "file",
            "size": 1012,
            "mtime": 1779788207,
            "mode": "644"
        },
        {
            "name": "user-uploads",
            "type": "dir",
            "size": 4096,
            "mtime": 1779789012,
            "mode": "644"
        }
    ]
}
```

`mtime` is **epoch seconds** (not milliseconds — matches the
other framework adapters Manyfold ships).

`type` is one of `file` / `dir` / `link`. A symlink is reported with
`type: "link"` and its `size` field reflects the symlink's own size,
not the target.

### Path-traversal safety

`path` must resolve (after `resolve(strict=False)`) to a path that's
either equal to or a descendant of the workspace root. Any escape
attempt (`../../etc`, absolute paths outside the root, symlinks that
resolve outside) returns:

```
403 {"detail": "path escapes workspace: '<original path>'"}
```

### Empty workspace special case

If the workspace dir doesn't exist on disk yet (freshly-provisioned
agent has never produced a file), the endpoint returns `entries: []`
with HTTP 200, NOT 404. This is intentional so Manyfold's chat-header
file tree can render cleanly the first time a user opens it.

For any **other** non-existent path within an extant workspace, the
endpoint does return 404.

---

## 11. `GET /manyfold/agents/{agent_id}/files/stat`

Single-file metadata. Used by the preview pane to size + content-type
sniff before fetching the body.

### Query parameters

| Param | Required |
|---|---|
| `path` | ✓ — absolute path |

### Response

```json
{
    "entry": {
        "name": "Bootstrap.md",
        "type": "file",
        "size": 1012,
        "mtime": 1779788207,
        "mode": "644"
    }
}
```

### Errors

| Status | Cause |
|---|---|
| 404 | Path doesn't exist. |
| 403 | Path escapes workspace. |

---

## 12. `GET /manyfold/agents/{agent_id}/files/read`

Stream a file's contents. Used by Manyfold's preview / download flow.

### Query parameters

| Param | Required |
|---|---|
| `path` | ✓ — absolute path |

### Response

`200` with body:

```
Content-Type: application/octet-stream
Content-Length: <size in bytes>
X-Accel-Buffering: no
```

Body is the raw file bytes, streamed in 64 KiB chunks. Manyfold's UI
sniffs the filename / extension to decide how to render (Markdown /
source / hex).

### Errors

| Status | Cause |
|---|---|
| 400 | Path is a directory. |
| 403 | Path escapes workspace. |
| 404 | Path doesn't exist. |
| 413 | File larger than 64 MiB — Manyfold's preview is for inspection, not unbounded blob transfer. Use the bundle export flow for large artifacts. |

---

## 13. `POST /v1/chat/completions`

OpenAI-compatible chat completions. Each call triggers a fresh agent
turn (`BackgroundRun`) inside NarraNexus and streams its events back
mapped onto OpenAI's chunk format.

### Request

The request body is a standard OpenAI chat completions request. The
fields we actually consume:

| Field | Used | Notes |
|---|---|---|
| `model` | ✓ | **Must be the NarraNexus `agent_id`**, not an LLM model name. NarraNexus resolves the actual model from the agent's `user_slots` config. |
| `messages` | ✓ | Standard OpenAI message list. Only the last `role: "user"` entry is fed as `input_content`; earlier turns come from NarraNexus's narratives store. Multimodal blocks have `text` parts extracted; image / audio / file parts are silently ignored. |
| `stream` | ✓ | Both `true` and `false` are supported. `true` is the primary path Manyfold uses. |
| `temperature` / `top_p` / `max_tokens` / `tools` / … | ✗ | Silently ignored. The agent's behavior is fully governed by its own configuration (modules, narratives, user_slots) — there's no per-call override path. |

### Streaming response — chunk shape

Standard OpenAI SSE: `data: {…}` newline-separated, `data: [DONE]`
terminator.

NarraNexus uses three OpenAI delta channels plus one non-standard
extension:

| Channel | Carries | OpenAI standard? |
|---|---|---|
| `delta.reasoning_content` | Agent's chain-of-thought (`agent_thinking` events + the LLM token stream in `agent_response` events before the reply tool fires) | OpenAI o1 / DeepSeek convention — standard for reasoning models |
| `delta.tool_calls[]` | Non-reply tools the agent invokes (`web_search`, `lark_cli`, `skill_module`, …) | Standard OpenAI |
| `delta.content` | The user-visible reply, populated when the agent calls `send_message_to_user_directly` | Standard OpenAI |
| `delta.tool_results[]` | Tool outputs paired with the matching `tool_call_id` we just emitted | **Non-standard** — NarraNexus extension, see below |

### The `delta.tool_results[]` extension

Vanilla OpenAI streaming routes tool results via a separate `role:
"tool"` message in the next turn — the client is expected to execute
the tool and re-post the result. NarraNexus executes tools internally
and knows the output right after the call, so we inline it on the
same assistant turn:

```json
{
    "choices": [{
        "delta": {
            "tool_results": [
                {
                    "tool_call_id": "call_a1b2c3d4...",
                    "content": "{\"results\": [...]}"
                }
            ]
        }
    }]
}
```

`tool_call_id` is FIFO-paired with the `tool_calls` IDs we generated
earlier in the same stream. Clients that don't recognise the field
ignore it; Manyfold's `openclaw.adapter.ts` chunk parser routes it to
a `tool_result` ChatStreamEvent.

If your client wants strict OpenAI compliance, just discard the field.
The tool calls will then show as "in progress" indefinitely from your
UI's POV, but the agent itself doesn't care.

### `finish_reason` policy

- `"stop"` — agent emitted user-visible content via
  `send_message_to_user_directly` (the common case).
- `"tool_calls"` — agent invoked tools but never produced user-visible
  text. We honor this so an OpenAI-spec strict client can detect it,
  but in practice NarraNexus's helper_llm fallback fills in a default
  reply so this almost never fires.

### Non-streaming response

When `stream: false`, NarraNexus accumulates the same events and
returns one synchronous `chat.completion` object:

```json
{
    "id": "chatcmpl-…",
    "object": "chat.completion",
    "created": 1779800000,
    "model": "<agent_id>",
    "choices": [{
        "index": 0,
        "message": {
            "role": "assistant",
            "content": "<combined user-visible reply>",
            "reasoning_content": "<combined thinking + agent tokens>",
            "tool_calls": [{ "id": "call_…", "type": "function", "function": { "name": "…", "arguments": "{}" } }],
            "tool_results": [{ "tool_call_id": "call_…", "content": "…" }]
        },
        "finish_reason": "stop"
    }]
}
```

`tool_results` here mirrors the streaming-mode extension for
symmetry; non-streaming is rarely used in practice but the contract is
worth keeping consistent.

### Errors

| Status | Code | Cause |
|---|---|---|
| 400 | `agent_not_found` | `model` doesn't match an agent row. |
| 400 | `no_user_message` | `messages` had no `role: "user"` entry. |
| 401 | `unauthenticated` | Bearer missing / wrong. |
| 500 | `api_error` | Mid-stream fatal error — emitted both as an OpenAI error JSON (non-streaming) or inline as a `[error]` content chunk followed by `data: [DONE]` (streaming). |

---

## 14. Fragment-auth URL pattern (Open Native UI)

Manyfold's "Open Native UI" button mints a URL of the form:

```
https://<runtime-ingress-host>/#token=<gateway>&user=mf_<id>&agent=<agent_id>
```

The fragment carries:

| Param | Meaning | Required for handoff |
|---|---|---|
| `token` | Gateway token (same value as the `Authorization: Bearer` value) | ✓ — this is the signal that triggers the handoff |
| `user` | Manyfold-side user id, **already prefixed with `mf_`** | ✓ for "log in as this user" semantics |
| `agent` | NarraNexus agent_id to deep-link to | optional — when supplied, the chat tab opens on this agent |

### Why a fragment, not a query string

Fragments are NEVER sent to the server in the HTTP request line — so
the gateway token can't leak into:

- nginx / load balancer access logs
- the TLS terminator's captured request line
- the browser's `Referer` header on subsequent navigations

NarraNexus's frontend parses the fragment at boot
(`frontend/src/lib/manyfoldFragmentAuth.ts`), logs the user in,
optionally calls `setAgentId(agent)`, then `history.replaceState`'s
the fragment out of the URL so a refresh or shared screenshot doesn't
keep the secret visible.

### Frontend behavior

1. Read fragment at module init (before React mounts).
2. If `token` is present, stash in module-local memory + log in via
   `configStore.login(user_id, token)`.
3. If `agent` is present, `configStore.setAgentId(agent)`.
4. Force `runtimeStore.setMode('local')` so the App's mode-select
   route guard doesn't bounce the user to `/mode-select`.
5. Scrub fragment via `history.replaceState(null, '', pathname +
   search)`.
6. Install a `hashchange` listener so a URL pasted into an
   already-open tab still triggers the handoff.

If the fragment is absent or doesn't contain `token` / `user` /
`agent`, the function returns immediately and no state is touched —
vanilla NarraNexus deployments (without Manyfold) are unaffected.

---

## 15. Identity & user mapping

Both sides keep their own `agents` table; the **join key is
`agent_id`** (Manyfold's `internal_id` field == NarraNexus's
`agents.agent_id` column).

### User-id namespacing

NarraNexus uses a single global `users` table, no namespace column. To
prevent Manyfold-originated users colliding with native users
(`bin`, `local-default`, …), every Manyfold user becomes
`mf_<sanitised_id>` on this side. The transformation is:

```
manyfold_user_id              → narranexus_user_id
─────────────────────         ──────────────────────
"user_local_admin"            → "mf_user_local_admin"
"user-123/abc"                → "mf_user_123_abc"
"mf_already_prefixed"         → "mf_already_prefixed"   (no double prefix)
```

Sanitisation rule: `[^a-zA-Z0-9_-]+` collapses to `_`, truncated to
60 chars, prefixed with `mf_` if not already.

### Multi-user-per-container

A single NarraNexus container can host multiple `mf_<…>` users +
native users side-by-side, each with their own narratives, providers,
and agents. The container is "single-tenant" in the Owner-decided
sense (one container = one Manyfold runtime owner), but the
NarraNexus-side multi-user model still applies inside.

### Agent ownership

`agents.created_by` is set at POST `/manyfold/agents` time and never
changes. If a Manyfold user creates an agent, the row is owned by
their `mf_<…>` user. If a Manyfold user opens the native UI (via
fragment auth) and creates an agent through *that* UI, the row is
owned by whichever user was logged in (typically also the `mf_<…>`
one carried in the fragment).

---

## 16. Idempotency, ordering, & failure semantics

| Operation | Idempotency | Failure handling |
|---|---|---|
| POST `/manyfold/agents` | Idempotent (UPSERT). Re-running with the same `agent_id` updates `agent_name` / `description` / `created_by`. | Throws on non-2xx. Caller should surface; partial state is recoverable by re-running. |
| PATCH `/manyfold/agents/<id>` | Idempotent (same patch = same outcome). | Throws on 404 / 5xx; caller aborts user-facing PATCH so the two sides stay in sync. |
| DELETE `/manyfold/agents/<id>` | Idempotent (404 == already gone == success). | Cascade is best-effort; partial failure is logged + reported in `cascade` map. |
| `/v1/chat/completions` | NOT idempotent — each call triggers a new agent turn. Repeating a request runs the agent twice. | Mid-stream fatal errors emit a `[error]` chunk + `[DONE]` then close. |
| `/files/*` (GET) | All read-only, trivially idempotent. | 4xx / 5xx as documented per endpoint. |

### Call ordering Manyfold relies on

The platform-side adapter (`narranexus-agent.adapter.ts` in
`netmind-cloud-agents`) assumes:

1. POST `/manyfold/agents` runs **before** any `/v1/chat/completions`
   for the same agent_id (else the chat completes returns 400
   `agent_not_found`).
2. PATCH `/manyfold/agents/<id>` runs **before** the Manyfold side
   commits its own DB rename (so a failure here aborts the whole
   rename + keeps the two sides consistent).
3. DELETE `/manyfold/agents/<id>` runs **after** Manyfold removes its
   own agent row (else a stale Manyfold row could try to chat with an
   agent NarraNexus already cascade-deleted; that returns 400
   `agent_not_found` cleanly, but it's wasted load).

The reconcile loop on the Manyfold side polls `GET /manyfold/agents`
every ~30 s and reconciles its `agents` table against the live list —
this is what auto-discovers agents created via the native UI and
heals state drift after partial failures.
