---
code_file: src/xyz_agent_context/module/lark_module/_lark_mcp_tools.py
stub: false
last_verified: 2026-07-10
---

## 2026-07-10 — PR #87 review: react tool body → shared helper

`react_to_user_message` now delegates to [[channel_reactions]] `best_effort_react`
(resolve semantic→token, call the SDK, best-effort envelope + log the failure);
only the per-platform `_LARK_REACTIONS` map stays here. The 11-name vocabulary
lives once in `channel_reactions.REACTION_VOCABULARY`.

## 2026-07-10 — react_to_user_message tool (agent-driven early feedback)

New agent-facing `react_to_user_message(agent_id, room_id, message_id, emoji)`.
`emoji` is a shared cross-channel semantic value from an 11-item "task mood" menu
(`on_it`/`searching`/`done`/`celebrate`/`thumbs_up`/`heart`/`thanks`/`applause`/
`hundred`/`warning`/`problem`; unknown → `on_it`) — the agent picks per task.
Each module maps it to its platform tokens (`_LARK_REACTIONS` → Lark `emoji_type`
keys), backed by `LarkCLIClient.add_reaction`. Best-effort: returns
`{success:false, reason}` on any error, never raises. The full menu + the
platform each renders lives in each channel's get_instructions.

## 2026-05-22 — add `lark_unbind` to close the bind/unbind symmetry

Agents on natural-language "解绑 / unbind / disconnect" intents
replied: "Lark module currently has no unbind tool, I cannot
disconnect directly." Slack already had `slack_unbind`; Lark had
`lark_bind`, `lark_setup`, `lark_status` but no symmetrical
`lark_unbind` — unbinding was reachable only via the HTTP route
`POST /api/lark/unbind`, which an agent can't call.

Added `lark_unbind(agent_id)` calling the freshly-extracted
`_lark_service.do_unbind`. The HTTP route was refactored to call the
same helper so the cleanup logic (CLI profile, workspace, DB row,
bus channel reap) doesn't duplicate.

The `lark_module.get_instructions` prompt now ships a
`_LIFECYCLE_LINE` (rendered in the bound-state path) and a parallel
note in `_NO_BOT_INSTRUCTION` telling the agent the tool exists and
to confirm intent before calling (iron rule #7 covers destructive
actions — unbind drops credentials + Inbox channel rows).

## 2026-04-23 (2/2) — trim docstring to hints + pointers

Second pass on the `lark_cli` docstring same day. Pass 1 inlined a
multi-step recap of the auth flow; pass 2 trims it back to "hint +
pointer" so the tool docstring doesn't compete with upstream SKILL
docs. The on-failure block now:
- Names `missing_scope` and sends the reader to the prompt's
  "Incremental scope authorization" section and
  `lark_skill(agent_id, "lark-shared", "SKILL.md")` for the
  authoritative contract. Calls out the bot-scope vs user-scope
  divergence in one line so agents don't dead-end a bot-scope
  error into a user-OAuth URL.
- Keeps the one-liner decoding for `authorization_pending`,
  `Command blocked` (with/without `--scope`), and `No Lark bot
  bound` — these are the actual short strings agents see and need
  translated before they can read anything else.

Philosophy fixed here: our docstrings are not a replacement for
upstream skill docs. They are (a) navigation hints and (b)
NarraNexus-specific overrides where our setup differs from a stock
global lark-cli install (per-agent workspace, MCP-mediated skill
reading, per-agent credential management via `lark_setup` /
`lark_bind`).

## 2026-04-23 — lark_cli "On failure" rewrite for missing_scope

The `missing_scope` recovery bullet in the `lark_cli` tool docstring
previously taught only `auth login --scope X --no-wait` with no mention
of the follow-up `auth login --device-code D` poll. Agents therefore
kept re-minting on every turn (demo_user_v1 incident 2026-04-22).
Rewrote the bullet to (a) reference the fuller "Incremental scope
authorization" section rendered by `lark_module.get_instructions` and
(b) summarize the two-step, two-turn rule inline for agents that read
tool docstrings before prompts. Also added an explicit translation for
`authorization_pending` so agents don't mistake it for a generic
failure that warrants a fresh mint.

No logic changed inside `lark_cli` itself — still a passthrough after
`validate_command` + `sanitize_command`. Intentionally kept pure prompt
fix, per decision "trust LLMs to get smarter given clearer prompts
before adding state-machine scaffolding".

## Why it exists

Registers Lark MCP tools on the FastMCP server. C-mini redesign (2026-04-22)
collapsed the lifecycle surface from 10 tools to **7**, with the four
permission-flow tools merged into one state-machine entry.

Current tools:
- `lark_cli` — main execution of arbitrary lark-cli commands
- `lark_setup` — Click 1: create NEW Lark app (agent-assisted)
- `lark_bind` — bind EXISTING app (user pastes app_id + secret)
- **`lark_permission_advance`** — single entry for the three-click
  authorization lifecycle (Click 2, Click 3, availability)
- `lark_enable_receive` — store App Secret so WebSocket subscriber can
  auto-reply (Phase 3)
- `lark_status` — health + Matrix self-heal from CLI state
- `lark_skill` — read any file from a lark skill pack (SKILL.md default,
  `path=` for references/routes/scenes/data files)

## Design decisions

- **Single `lark_cli` tool** for all domain operations. Agent learns syntax
  from `lark_skill` + Module instructions rather than a tool per operation.
- **Three-click authorization in ONE tool** (`lark_permission_advance`).
  Previously 4 tools (`lark_configure_permissions`, `lark_auth`,
  `lark_auth_complete`, `lark_mark_console_done`) — their docstrings
  contained cross-tool "MANDATORY" directives that collided with
  `get_instructions` coach, making the Agent stall at Click 3. The state
  machine lives in one `event` parameter (`""` | `"admin_approved"` |
  `"user_authorized"` | `"availability_ok"`), so docstring conflicts are
  now structurally impossible.
- **`event` dispatched via `_advance_*` helpers** (module-level async
  functions) so tests can target each transition without going through
  `register_lark_mcp_tools`. Top-level tool body only handles guards
  (completed-state, unknown event) and delegates.
- **User-facing messages as module constants** (`_MSG_*`). Tool returns
  them in `data.user_facing_message`; Agent sends verbatim. Keeps wording
  identical across agents and turns — no per-Agent drift.
- **Idempotent `event=""`**: if `admin_request_url` already exists, return
  it instead of re-running `auth login --no-wait` (which would invalidate
  the URL the user may already have open).
- **Completed-state guard**: `admin_approved` / `user_authorized` on an
  already-completed credential returns a harmless no-op with a
  `user_facing_message` telling the Agent to check the Matrix.
- **Security via `_lark_command_security`** — `validate_command` +
  `sanitize_command` before every `lark_cli` call; unchanged from
  pre-redesign.
- **Self-heal in `lark_cli` and `lark_status`**: a successful `--as user`
  call proves OAuth is live, so flip `user_oauth_completed_at` +
  `bot_scopes_confirmed` + `console_setup_done_at` without waiting for an
  explicit `user_authorized` event. Keeps Matrix truthful even if Agent
  skipped a ceremony step.

## Upstream / downstream

- **Upstream**: `lark_module.py` calls `register_lark_mcp_tools(mcp)` from
  `create_mcp_server`.
- **Downstream**: `_lark_credential_manager.py`
  (`current_click_stage`, `patch_permission_state`, `update_auth_status`);
  `_lark_command_security.py` (`validate_command`, `sanitize_command`);
  `_lark_workspace.py` (`build_profile_name`, `ensure_workspace`,
  `get_home_env`); `lark_cli_client.py` (`_run_with_agent_id`);
  `_lark_skill_loader.py` (called from `lark_skill`);
  `_lark_service.py` (`do_bind` for `lark_bind`).

## Gotchas

- **Click 2's device_code is NEVER poll-able**. It was minted by the
  `auth login --domain all --recommend --no-wait` call that seeds the
  submit-to-admin URL. Passing it to `auth login --device-code` returns
  `authorization_pending` forever (or `expired` after a while). The tool
  writes it to DB as `admin_request_device_code` and NEVER reads it back
  for polling — the only thing we ever poll is `user_authz_device_code`,
  which is minted fresh by `event="admin_approved"`.
- **`lark_setup` writes `app_id="pending_setup"` + `is_active=False`**
  before forking the background finalizer. `hook_data_gathering` in
  `lark_module.py` now injects `lark_info` for this pending row (P4 fix);
  without that fix, Agent sees "No Lark bot bound" for the ~15s window
  and tries to call `lark_setup` again.
- `_finalize_setup` is a fire-and-forget `asyncio.create_task` named
  `lark_finalize_setup:{agent_id}`, with a `done_callback` that logs
  exceptions at ERROR level. Without the callback, exceptions during the
  15-minute wait would silently vanish into `Task.exception()`. Symptom
  of a bug there: "Lark authorized but bot never goes ready."
- **Removed tools** (`lark_configure_permissions`, `lark_auth`,
  `lark_auth_complete`, `lark_mark_console_done`) are referenced only in
  legacy log lines and the file docstring's "replaces" list. Do NOT
  resurrect them as aliases — the whole point of the redesign is that
  there's one entry and no possible docstring collision.
- **`_tool_policy_guard.py:215`** still lists MCP tools in its Bash-block
  error text. When adding/removing lifecycle tools, update that list too
  (grep for `mcp__lark_module__` outside this file).
- **`lark_skill` is the ONLY FS reach** for Agents into Lark skill docs.
  The MCP container has the files at `~/.agents/skills/`; the Agent's
  workspace sandbox does not. Docstring + `lark_module._build_skill_section`
  system prompt both spell this out; `_lark_skill_loader` also prepends a
  banner and rewrites all in-doc links into `lark_skill(..., path=...)`
  calls so the Agent never falls back to `Read`.
