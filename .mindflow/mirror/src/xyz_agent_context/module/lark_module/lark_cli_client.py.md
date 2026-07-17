---
code_file: src/xyz_agent_context/module/lark_module/lark_cli_client.py
stub: false
last_verified: 2026-07-10
---

## 2026-07-10 — add_reaction runs `--as bot` (identity fix)

`add_reaction` now passes `--as bot` explicitly. The workspace holds both a bot
token and (post three-click) the owner's user OAuth token; the raw
`im reactions create` command defaults to the USER token when present, so the
reaction was showing under the human owner's name instead of the bot. Unlike the
`+messages-send` shortcut (which forces bot on its own), the raw reactions
command needs the flag. Requires the bot app to hold
`im:message.reactions:write_only` — without it the reaction now silently fails
(best-effort) rather than falling back to the user identity.

## 2026-07-10 — PR #87 review: emoji_type key source

The `emoji_type` keys the react tool maps to (`Typing`/`GLANCE`/`DONE`/`ERROR`/
`CrossMark`/…) come from Lark's fixed emoji enum — authoritative list:
https://open.larksuite.com/document/server-docs/im-v1/message-reaction/emojis-introduce
A wrong key surfaces as a silent `{success:false}`, so keep the module's
`_LARK_REACTIONS` map in sync with that doc.

## 2026-07-10 — add_reaction (backs react_to_user_message)

New `add_reaction(agent_id, message_id, emoji_type) -> reaction_id`, routed
through the same per-agent `_run_with_agent_id` path as `send_message` (workspace
HOME isolation, hydrated credential). Shells out to `lark-cli im reactions
create` (`--params`/`--data` JSON), validates `message_id` against
`_LARK_ID_PATTERN`, raises `RuntimeError` on CLI failure so the react tool can
log + swallow (best-effort). `_extract_reaction_id` digs the id out of either the
direct or `data`-wrapped payload. Consumer: `_lark_mcp_tools.react_to_user_message`.

## 2026-05-28 — set CWD to agent workspace when spawning lark-cli (P0 fix)

Production bug 2026-05-28: NarraNexusPM agent ran `vc +notes
--minute-tokens X` to grab a Lark transcript. lark-cli wrote the
transcript to `./artifact-<title>/transcript.txt` (per the lark-vc
SKILL docs — `--output-dir` defaults to `.`). The agent's `Read`
tool then tried to load it and failed with "outside agent
workspace" — because the subprocess inherited the MCP container's
CWD (`/app/`), not the agent's workspace at
`/opt/narranexus/workspaces/<agent>_<user>/`.

I had initially mis-attributed the cause to HOME (the lark workspace
isolation). HOME is what `lark-cli` uses for config + OAuth tokens
— it isn't where downloads go. **Downloads go to CWD.** That's the
critical detail.

### Fix
1. Module-level helper `_resolve_agent_workspace_cwd(agent_id, db)`
   resolves `agents.created_by` → `user_id`, computes the workspace
   path via `attachment_storage.get_workspace_path(agent_id, user_id)`,
   ensures it exists, and returns the `Path`. Result is cached in
   `_agent_user_id_cache` (immutable per agent) so subsequent calls
   don't re-query the DB.
2. `_run_with_agent_id` calls the helper and forwards the result as
   the new `cwd=` parameter on `_exec_lark_cli`.
3. `_exec_lark_cli` passes `cwd=str(cwd) if cwd else None` into
   `asyncio.create_subprocess_exec`.

### Volumes (confirmed via `docker inspect narranexus-mcp`)
Both the MCP container (writer) and the backend container (reader)
mount the same `narranexus-app_workspaces` Docker volume at
`/opt/narranexus/workspaces` → writing from MCP and reading from
backend works without any further plumbing.

### Generalization
The same pattern applies to ANY future MCP tool that shells out to
a CLI which writes default-relative paths: pass `cwd=agent_workspace`
when spawning. Audited callers of `create_subprocess_exec` /
`subprocess.run` across the module/ tree (2026-05-28):
- `_lark_event_probe.py` — health probe, no file outputs (skipped)
- `_lark_mcp_tools.py::_finalize_setup` — pre-bind, no agent
  workspace exists yet (intentionally inherits parent CWD)
- `common_tools/web_search_*` — read stdout, no file outputs
- `skill_module::install_skill` — uses tempfile + shutil.move into
  the agent workspace explicitly (already correct)

Only `_run_with_agent_id` needed the CWD fix.

### Regression / E2E tests
- `tests/lark_module/test_lark_cli_cwd.py` — 7 tests:
  - cwd kwarg threads to `create_subprocess_exec`
  - cwd=None preserves legacy behaviour
  - `_resolve_agent_workspace_cwd` happy path + cache + orphan +
    DB error fallback
  - end-to-end: real Python helper subprocess writes
    `./marker.txt` inside the cwd we asked for (proves the OS-level
    plumbing, not just the kwargs)

## 2026-05-21 — `get_user` defaults to `--as user`

`get_user` now runs `contact +get-user --as user` (was `--as bot`). The
bot tenant token lacks `contact:user.base:readonly`, so `--as bot`
returns only `open_id`/`union_id` with no `name` for **every** sender —
which made `LarkTrigger.resolve_sender_name` fall back to "Unknown" for
everyone (and the agent then guessed names from its roster). Each
lark-configured agent already holds the owner's user token in its
isolated HOME (from the three-click auth), and that token *can* read
names, so we resolve through it. `identity="bot"` stays selectable for
callers that genuinely want the app identity.

## Why it exists

Unified async wrapper around every `lark-cli` subprocess call. Turns the
CLI into a single-function API: call `_run_with_agent_id(args, agent_id)`
and the client handles credential lookup, workspace hydration, and HOME
isolation transparently.

## Design decisions

- **DB is source of truth, workspace is derived.** Every agent's Lark
  state (app_id, plain app_secret, profile name, brand) lives in
  `lark_credentials`. The per-agent workspace (`~/.narranexus/lark_workspaces/<id>/`)
  is a view that can be rebuilt from DB at any time.
- **`_ensure_hydrated(cred)` is idempotent.** Before every agent-scoped
  call we check `workspace/.lark-cli/config.json` — if it already lists
  `cred.app_id`, we skip; otherwise we rebuild by running
  `lark-cli config init --app-id X --app-secret-stdin --name Y --brand Z`
  with HOME=workspace. Plain secret flows DB → stdin → CLI, never
  touches args.
- **Single workspace, single profile, no `--profile` flag.** Because
  each workspace contains exactly one active profile, we never need
  `--profile` on subsequent commands. This matches how a single-machine
  user naturally uses `lark-cli`.
- **Lazy migration for legacy manual binds.** Pre-refactor manual binds
  had `workspace_path=""` in DB. On first call, `_run_with_agent_id`
  computes the path, persists it, and hydrates — no startup migration
  script needed.
- **`_run_with_home` kept for one special case** — `config init --new`
  during `lark_setup` creates the credential itself, so it runs before
  any DB row exists and bypasses hydration.
- **`shell=False` everywhere**, secrets via `stdin_data`, timeout kills
  the subprocess. All unchanged from V1.

## Upstream / downstream

- **Upstream**: every `_lark_mcp_tools.py` tool, `_lark_service.do_bind`
  (which uses `_run_with_agent_id` to verify credentials by hitting bot
  info), `lark_trigger.py` (for `get_user`, bot open_id lookup,
  `_resolve_sender_name`), `lark_context_builder.py` (`list_chat_messages`),
  `lark_module.py` (`send_message`), `backend/routes/lark.py` (unbind),
  `backend/routes/auth.py` (delete_agent).
- **Downstream**: `lark-cli` binary, `_lark_workspace.py` (paths + HOME
  env), `_lark_credential_manager.py` (cred fetch, lazy migration
  persistence).

## Gotchas

- Hydration triggers a real `config init` subprocess. First call on a
  cold workspace can take a couple of seconds. Subsequent calls are fast
  (idempotence check is a single file read + JSON parse).
- If DB has no plain secret (agent-assisted setups before
  `lark_enable_receive`), hydration fails deterministically with an
  actionable error telling the caller to complete Phase 2.
- Debug logs include the full `lark-cli` command. Secrets are passed via
  `stdin_data`, so they never appear in logs — keep it that way.
- `_exec_lark_cli` is private; external callers must go through
  `_run_with_agent_id` (typical), `_run_with_home` (config init --new),
  or the business methods (`send_message`, `get_user`, etc.).

## Phase 1c additions — binary download path

- **`capture_binary` kwarg on `_exec_lark_cli` (+ forwarded by
  `_run_with_agent_id`).** When set, stdout is treated as a status
  channel rather than a JSON payload. lark-cli's `api ... --output
  <path>` writes the response body to disk and emits an empty stdout
  on success (or a JSON error envelope on failure); parsing an empty
  string as JSON would have raised, so the new mode skips that step
  and returns `{"success": True}` with no `data` field. Error handling
  is unchanged — non-zero exit still surfaces the JSON error envelope
  the same way the text path does.

- **`fetch_message_resource(agent_id, *, message_id, file_key,
  resource_type, timeout=60.0) -> bytes`.** Async wrapper around
  `api GET /open-apis/im/v1/messages/{id}/resources/{key}` with
  `--params {"type": "..."}` and `--output <tmpfile>`. Reads the
  tmpfile back as bytes, cleans up in `finally` (so both success and
  error paths leave no leaked temp files). Raises `RuntimeError` on
  CLI failure / empty output / unreadable file — the trigger's
  `fetch_attachments` catches and audits, preserving never-raise at
  the trigger boundary.

  `resource_type ∈ {"file", "image", "audio", "video", "media"}` per
  Lark's IM resource endpoint contract.

  Uses `tempfile.NamedTemporaryFile(delete=False)` to obtain a path
  the CLI can write to under the current process's `/tmp`; explicit
  `os.unlink` in `finally`. The PRP plan flagged the option to use
  the workspace's tmp subdir under disk-pressure, but the default
  is fine for most attachments and avoids a hard dependency on the
  workspace being hydrated before fetch.
