---
code_file: plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/_lark_mcp_tools.py
stub: false
last_verified: 2026-08-11
---
## 2026-08-11 (三轮审查) — lark_setup 的 seeded 失败早返回先杀 proc

`lark_setup` 里 `_put_cred` 失败的早返回（本身是修静默成功加的）会**跳过收割 `proc` 的 _finalize_setup 任务**——`proc` 是等浏览器流程的 `lark-cli config init --new`，云端 backend 不可达时每次重试都在容器里留一个僵尸子进程 + 占 workspace。改为返回前 `proc.kill()`+`await proc.wait()`（照抄同块内 URL 超时/抽不出 URL 两处早返回的既有姿势）。

## 2026-08-11 (二轮审查) — 补齐剩余 must-succeed 写检查

独立复审又抓到几处静默成功：`_advance_user_authorized` 的**完成标记写**（`user_oauth_completed_at`/`bot_scopes_confirmed`——`current_click_stage()` 据此判 "completed"）原来 auth_status flip 检查了、紧接着的 permission_state 完成写没检查 → 云端失败会返 `stage_after="completed"` 但库里还是 `waiting_user_click`。已加检查。`_advance_admin_approved`(存 user_authz_device_code)、过期重生成 URL、`_advance_availability_ok`(可选标志) 也一并 guard。至此**全部 advance/mint 写点都检查信封**；仅 lark_status/lark_cli 自愈写（每调用重写）保持 best-effort。守卫测试 [[test_lark_permission_advance]]。

## 2026-08-11 (审查收口) — 写助手返回信封，调用点检查 success（不再静默成功）

4 个写助手从 `-> None` 改 `-> dict`，**原样返回 seam 信封**。seam 永不抛（HttpStore 把 unreachable/非2xx/非JSON 降级成 `{success:False}`；DirectStore 现在也 catch→信封），所以「返 None、丢掉信封」= 云端写失败被静默当成功、工具骗 agent「已写入」（铁律 #5 / incident #3）。修：`_advance_start`(device_code 必存)、`lark_enable_receive`(app_secret 必存)、`lark_setup`(pending 行必存)、`_finalize_setup` step4(finalize 失败→清 pending 行) 全部检查 `success`、失败返结构化 error；`_delete_cred` 清理保持 best-effort（seam 内部已 log）。另：bot identity 回写用 `{k:v for … if v}` 过滤空值，恢复旧 update_bot_identity 的「空不覆盖好值」保护（deep_merge 标量 patch 胜出，`""` 会抹掉）。守卫测试 [[test_lark_permission_advance]] 新增「seam 写失败→工具报错非静默成功」。

## 2026-08-11 (lark 写迁移) — 全部写点走 ChannelCredentialStore seam

三击 OAuth 的 CLI 子进程 + 轮询留本地(纯计算)，只 DB 持久化经 seam：4 个薄助手 `_patch_ps`(permission_state 深合并)/`_patch_fields`(顶层列)/`_delete_cred`/`_put_cred` 包装 patch/put/delete 原语，替换掉全部 `get_mcp_db_client()+LarkCredentialManager(db).*`。lark_bind→`seam.bind`(do_bind 经 /api/lark/bind)、lark_unbind→`seam.unbind`(do_unbind 含 inbox 拆除)。**本文件 get_mcp_db_client==0**。删 XYZBaseModule/LarkCredentialManager 死 import。

## 2026-08-11 (PR-F) — 读凭据 + agent 名改走 ChannelCredentialStore seam

`_get_credential` 改 `get_channel_credential_store().get_credential("lark", …)` → `_cred_from_raw` 重建（cred.get_app_secret()/permission_state/current_click_stage() 用法零变化）；`_get_agent_name` 改 seam `get_agent_name`。**写/CLI 大量留尾**：三击 OAuth 全流程（_finalize_setup/_advance_*/lark_setup/lark_bind/lark_unbind/lark_enable_receive/delete_credential）仍 `LarkCredentialManager(db)`——这些是 **CLI 子进程驱动、无后端路由** 的写，是 #2 里唯一真正卡住 strip DB_PASSWORD 的硬骨头，需另立后端基础设施，本期不动（见 [[channel_store]] 已知缺口 + 迁移 spec）。


## 2026-08-04 — lark_cli docstring 增补消息正文规则

配合 [[_lark_command_security]] 的 @file 守卫，docstring 新增
--text/--markdown 段：① 多词正文必须整体引号包裹（未加引号会被
positional-arguments 拒绝）；② 这两个 flag 不读文件，禁止先 Write
文件再引用——正文永远内联。动机：claude_code 链路模型的编程习惯
（写文件解决问题）漏进 IM 场景，工具描述是压制它杠杆最高的 prompt 面。

## 2026-07-29 — bot identity, sensitive-scope guidance, skill list

Three smaller changes landing with the scope work above.

**`_finalize_setup` switched to `/open-apis/bot/v3/info`.** It called
`contact +get-user --as bot`, which with bot identity returns
open_id/union_id and NO name (see `LarkCLIClient.get_user`) — so its
`if name:` guard never fired and every setup-created bot kept an empty
`bot_name`. The new call matches `do_bind` in [[_lark_service]] and also
yields `bot_open_id`, which [[lark_trigger]]'s group @-mention gate
matches on.

**`_MSG_GROUP_SCOPE_GUIDE` / `_group_scope_guide(cred)`** render the one
step the three-click flow structurally cannot automate: enabling
`im:message.group_msg` in the console and publishing a version. Returned
from `lark_enable_receive` (the moment group behaviour starts to matter)
and from `lark_status` (because "the bot answers off-topic in groups"
arrives weeks later). Written around READING rather than replying —
users who read it as "the bot will start answering everything" refuse
the scope, and the @-mention gate makes that fear unfounded.

**`lark_skill`'s skill roster** was stale against upstream (missing
lark-vc-agent, lark-note, lark-markdown, lark-apps). Refreshed, plus an
explicit note that the loader discovers whatever is installed and the
docstring list is a hint, not the source of truth — upstream adds skills
on their own cadence and this list will drift again.

## 2026-07-29 — first-time binding requests the full user-scope set

`lark_setup`'s three-click flow used to mint both Click 2 and Click 3 with
a bare `auth login --domain all --recommend`. `--recommend` filters to
lark-cli's auto-approve registry (`scope_priorities.json` recommend=="true"
minus `scope_overrides.json` deny), which drops ~33 scopes the CLI-created
PersonalAgent app carries on its user side. Agents therefore hit
`missing_scope` on everyday calendar / mail / search work, and each gap
cost the owner one mint → click → poll top-up round.

`_EXTRA_LOGIN_SCOPES` (32 entries) now rides on an explicit `--scope`
argument so the whole set is requested in the SAME approval pair. The
trade is deliberate: these are precisely the scopes that are NOT
auto-approve, so on an enterprise tenant Click 2 becomes a real admin
request covering them — one approval up front instead of a trickle of
interruptions. Partial approval stays survivable: the CLI's
`ensureRequestedScopesGranted` reports the ungranted remainder and the
incremental `auth login --scope X` path (allowed by
[[_lark_command_security]]) still covers the rest.

**Both clicks mint from one constant (`_LOGIN_MINT_ARGS`) on purpose.**
The token's scope is whatever CLICK 3 asked for; when the args lived as
two separate literals, widening only Click 2 would widen the approval
request and then discard the extra grants at mint time, with no error
anywhere. `test_login_mint_args.py` pins the two call sites equal.

**Bot-identity scopes are structurally out of reach here and must not be
added to the list.** `auth login` only ever grants user identity. The one
that bites: `im:message.group_msg` ("获取群组中所有消息", flagged SENSITIVE
by Lark) decides whether the bot receives every group message or only
@-mentions — it is enabled in the developer console, shipped by
publishing a new app version, and reviewed by the tenant admin. Its
user-identity twin `im:message.group_msg:get_as_user` is a different
thing (the agent reading group history as the owner) and already arrives
via `--recommend`. Requesting the bot-side name here would silently never
be granted. Same for `im:message:send_as_bot` / `im:resource`.
`im:message.send_as_user` is excluded on product grounds — the agent
speaks as the bot, never impersonates the owner.

**CLI floor: lark-cli >= 1.0.31.** Older versions hard-fail with "cannot
use --scope together with --domain/--recommend" — the flags were mutually
exclusive until then and only became additive in 1.0.31. The desktop
bundle pin moved 1.0.18 → 1.0.79 in the same change; cloud already
tracked latest. A future downgrade below the floor breaks Click 2
outright (铁律 #7).

## 2026-07-24 — setup residency (B++): zero-arg setup tools return the guide

`lark_setup` with `brand=""` and `lark_bind` missing app_id/app_secret now
return `{"success": True, "setup_guide": _NO_BOT_INSTRUCTION}` instead of a
"required" error — the full walkthrough left the system prompt ([[lark_module]]
unbound one-liner) and is served here on demand. Invalid-brand errors are
unchanged. The walkthrough text stays in its original constant; lazy import
avoids a module-import cycle.

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
platform each renders lives in each channel's contribute_instructions.

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

The `lark_module.contribute_instructions` prompt now ships a
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
authorization" section rendered by `lark_module.contribute_instructions` and
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
  `contribute_instructions` coach, making the Agent stall at Click 3. The state
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
  before forking the background finalizer. `gather` in
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
