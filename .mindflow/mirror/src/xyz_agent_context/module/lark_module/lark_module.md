---
code_file: src/xyz_agent_context/module/lark_module/lark_module.py
stub: false
last_verified: 2026-05-20
---

## 2026-05-20 — `_INCREMENTAL_AUTH_GUIDE`: bot-scope dead-end

Extended the `--as bot` `missing_scope` bullet and added a "verify
before declaring solved" bullet. Why: prod agent `agent_94360f6c4b98`
(owner Xiong) hit `99991672 App scope not enabled` on a `--as bot`
minutes call; the owner was repeatedly handed `auth login` URLs that
can only grant USER scopes, so clicking never fixed the bot/app scope
(which needs a developer-console enable **plus a new app-version
publish**, and possibly admin approval). The agent also recorded
"授权已解决" in narrative memory without re-running the failing call.

The guide now teaches: (1) `auth login` / a user click can never grant
a bot scope — "clicked but still fails" is the expected symptom, stop
minting URLs; (2) console scope changes need a version publish to take
effect; (3) re-run the actual failing command to confirm success
before claiming resolved / writing it to memory. Kept general (no
`minutes`-specific wording) per CLAUDE.md iron rule #4. Regression
pins in `tests/lark_module/test_incremental_auth_guide.py`
(3 new tests, 2026-05-20 block).

Follow-up bullet: an **incremental scope top-up is NOT the three-click
binding flow** — `lark_permission_advance` is binding-only and its
`Already completed` ≠ the needed scope is granted; a top-up is only
`auth login --scope`(mint)→`auth login --device-code <carried code>`(poll).
Why: prod Xiong minutes saga — agent called `permission_advance` every turn,
read "Already completed" as success, re-minted instead of polling the
device_code carried in its reasoning. (Carry works — reasoning is spliced
back across turns via meta_data.reasoning; the agent took the wrong flow.) +1 test.

Third bullet (from a live test of a freshly-bound agent): once the scope is
satisfied, a downstream Lark API error (`403 permission deny` / `failed to
query`) is NOT an auth problem — more auth/clicks won't fix it (resource-level
/ Lark-side / lark-cli). Agent must stop minting/re-polling and tell the user.
Why: after scopes were granted, `vc +notes --minute-tokens` returned `ok:true`
(scope) then `403 permission deny` on the specific minute, and the agent
looped retrying to timeout. +1 test.

## 2026-05-08 — Phase 2: subclass `ChannelModuleBase`

`LarkModule` now inherits `xyz_agent_context.channel.ChannelModuleBase`
(Phase 2 of the IM channel abstraction). The structural boilerplate
moves into the base; the 600+ lines of Lark-specific instruction
rendering + 7 MCP tools stay here.

### What moved into `ChannelModuleBase`

- Sender registry self-registration (`ChannelSenderRegistry.register`
  called automatically in base's `__init__`). The class-level
  `_sender_registered` flag is gone — base owns the once-per-channel
  guard.
- `hook_data_gathering` template (loads credential → calls
  `build_extra_data` → injects into `ctx_data.extra_data[ctx_data_key]`).
  Lark's `lark_info` dict construction lives in `build_extra_data` now.
- `hook_after_event_execution` filtering by `working_source`. The
  body of post-execution work is in the new `_on_event_executed`
  override hook.
- `get_mcp_config` (built from class attrs `mcp_server_name` + `mcp_port`).
- `create_mcp_server` (FastMCP creation + `register_mcp_tools` call).

### What stays here (Lark-specific content, not boilerplate)

- All instruction constants: `_NO_BOT_INSTRUCTION`,
  `_THREE_CLICK_BACKGROUND`, `_IDENTITY_GUIDE`,
  `_INCREMENTAL_AUTH_GUIDE`, `_NARRANEXUS_SPECIFICS`,
  `_CONTENT_DELIVERY_GUIDE`, `_IRON_RULES`.
- `get_instructions(ctx_data)` — 600+ line three-click-flow renderer.
- `register_mcp_tools(mcp)` — calls `register_lark_mcp_tools(mcp)` to
  register all 7 Lark MCP tools.
- `build_extra_data(cred, ctx_data)` — builds the `lark_info` dict
  consumed by `get_instructions`. The `is_owner_interacting`
  trust-signal derivation reads `ctx_data.extra_data["channel_tag"]`,
  which is why the base passes `ctx_data` into this method.
- `send_to_agent(agent_id, target_id, message, **kw)` — Lark-specific
  delivery via `LarkCLIClient.send_message`.
- `_dev_console_url`, `_build_skill_section`, `_on_event_executed`.

### Class attrs setting the contract

```python
channel_name = "lark"
brand_display = "Lark / Feishu"
working_source = WorkingSource.LARK
ctx_data_key = "lark_info"
mcp_server_name = "lark_module"
mcp_port = LARK_MCP_PORT  # 7830
```

### Iron rule alignment

- Iron rule #6 (no DB destructive changes): no schema changes.
- Iron rule #7 (run.sh / DMG aligned): no service-definition changes.
- Iron rule #10 (mirror md): updated.

---

## 2026-04-23 update (4/4) — `_INCREMENTAL_AUTH_GUIDE` 加 admin-approval 两阶段说明

第四轮修改。2026-04-23 agent_bbddea03706e 的增量授权会话里，当 user 第一次
点链接时 Lark 服务器返回 `authorization failed: ... pending approval`——
企业租户对新 scope 的默认行为是**管理员必须先审批 scope 进入 app**，
然后才能做用户级授权。这是**两个不同的 URL / 两次点击**，不是一次性的。

现状：Agent 只知道"mint 一次 → 让 user 点一次"，第一次把话说死
（"点完我就去查"），之后 poll 失败就重 mint，user 端体验是"刚点完又让我点"。

修法：`_INCREMENTAL_AUTH_GUIDE` 第二个 bullet 之后新加一条 bullet 讲：
- 对 enterprise tenant 的新 scope，**第一个 URL 可能是 admin 审批请求**
- 不要承诺 "click once and done"；告知 user 可能需要 admin 先批
- 看到 poll 返回 `pending approval` 时**不要立刻重 mint**，等 user 确认 admin
  批过再 mint 新的
- Admin 批过之后的那次 `--no-wait` 拿到的 device_code 才是能换 user
  token 的那个

测试 pin 在 `tests/lark_module/test_incremental_auth_guide.py::
test_guide_warns_about_admin_approval_preceding_user_authorization`——
断言 guide 提到了 admin approval、pending approval 错误、以及禁止
"click once" 类话术。

## 2026-04-23 update (3/3) — `_INCREMENTAL_AUTH_GUIDE` 加 "把 device_code 写进 reasoning" 提醒

第三轮修改，配合**跨 turn reasoning 持久化**（见
`.mindflow/mirror/src/xyz_agent_context/module/chat_module/chat_module.py.md`
2026-04-23 那一段）。

`_INCREMENTAL_AUTH_GUIDE` 末尾追加一条 bullet：mint 完 `--no-wait` 之后，
要把 `device_code`、scope、`verification_url` 显式写进自己的 reasoning
里，因为 tool-call output 单 turn 就消失。如果不 restate，下一轮 Agent
拿不到 `device_code` 值，只能重新 mint 一次——orphan 用户刚点过的 URL，
陷入 demo_user / the operator 今天经历过的死循环。

动机：2026-04-23 线上 session `agent_7f357515e25a` 里 Agent **理解**
机制（它自己诊断出来了），但还是循环，因为 tool output 里的
`OaEmm_C8Jy40…` 100 字节 opaque 串下一轮丢失。最后 user 把 device_code
当人肉 relay 传回去才解的围。现在 reasoning 跨 turn 保留了，Agent 只要
按这条 bullet 做就能自救。

## 2026-04-23 update (2/2) — prompt rewrite: hint-oriented, NarraNexus-aware

Second pass on the same day. The first pass (below) was too
prescriptive ("MUST specify --as", step-by-step auth scripts) and
was missing coverage for bot-scope recovery, scope accumulation, and
the NarraNexus-specific ways we diverge from stock lark-cli
(per-agent workspace isolation, no global filesystem access to
skill files). Rewrote in a hint-oriented register, explicitly
pointing agents at `lark_skill(agent_id, "lark-shared", ...)` and
the per-domain skill docs for details we deliberately don't
duplicate inline.

### What changed in this pass

- **`_IDENTITY_GUIDE`** relaxed from "Every write command MUST specify
  `--as` explicitly" to a starting-orientation: which identity is
  right for which kind of action, and a pointer to the domain skill
  docs when in doubt. The absolute `MUST` was getting in the way of
  legitimate user-only APIs like `im +messages-search`.
- **`_INCREMENTAL_AUTH_GUIDE`** rewritten:
  - Dropped the Step 1 / Step 2 script in favour of bullet-style
    reminders. Less "orders", more "things that trap agents".
  - Added explicit bot-scope vs user-scope branch. Previously any
    `missing_scope` pushed the agent onto the `auth login --scope X
    --no-wait` path, which is a dead end for bot scopes (they must
    be opened at the Lark developer console; the error response
    usually carries a `console_url`). the operator's case happened to be
    user-scope so this wasn't visible, but a bot-scope the operator would
    have been stuck minting URLs the user can never redeem.
  - Added "scopes accumulate across logins" — avoids the
    anti-pattern of re-requesting already-granted scopes every
    time.
  - Explicit pointer at `lark_skill(agent_id, "lark-shared",
    "SKILL.md")` for the authoritative contract; the inline bullets
    are what we've seen agents miss even when the skill doc is
    loaded.
- **New `_NARRANEXUS_SPECIFICS` section** (gated on stage=completed).
  Calls out the two ways our setup diverges from the assumptions
  baked into upstream SKILL.md:
  - Lark skill files are MCP-container-side, not filesystem-side;
    `Read`/`Glob`/`Grep` can't see them. The skill files themselves
    still carry "CRITICAL — MUST use Read to read ../lark-shared/"
    instructions (upstream-authored, not patchable from our side).
    This section is how we override those without touching the
    files.
  - Auth is per-agent, not global. Upstream "re-run `lark-cli
    config init` globally" guidance is about host installs;
    `lark_setup` / `lark_bind` MCP tools manage per-agent
    credentials for us.
- **Iron rule #7 added** — "Confirm before destructive /
  broad-reach writes." Deleting a doc, cancelling a meeting,
  removing a chat member, broadcasting to a large group, editing
  shared artifacts. The previous six rules covered impersonation,
  secrets, and untrusted input, but not high-blast-radius
  destructive action. `--dry-run` surfaced as the recommended
  preview mechanism.
- **`lark_cli` tool docstring "On failure" block trimmed** —
  previously tried to restate the two-step auth flow inline. Now
  just points at the prompt's "Incremental scope authorization"
  section and the `lark-shared` SKILL, with the five concrete
  error-code branches kept as a one-liner each (missing_scope,
  authorization_pending, Command blocked with/without --scope,
  No Lark bot bound). Our docstring is hints + NarraNexus-specific
  overrides, not a replacement for upstream SKILL docs.

### Token budget impact (estimated)

Stage=completed prompt gained ~400 tokens (new
`_NARRANEXUS_SPECIFICS`, new iron rule) and lost ~200 tokens
(trimmed `_INCREMENTAL_AUTH_GUIDE`, lighter `_IDENTITY_GUIDE`).
Net ~+200 tokens over pass 1. Acceptable given the coverage gaps
closed.

### Tests pinning this

`tests/lark_module/test_incremental_auth_guide.py` now covers 12
assertions:
- 5 from pass 1 (two-step flow phrasing, gating on stage=completed,
  no re-minting, etc.)
- Bot-scope vs user-scope branching present
- Scope accumulation taught
- Guide references `lark_skill(agent_id, "lark-shared")`
- Iron rule #7 (destructive confirm) present
- NarraNexus-specifics section teaches workspace isolation +
  `lark_skill` pointer
- NarraNexus-specifics section teaches per-agent auth (names
  `lark_setup` / `lark_bind`)
- NarraNexus-specifics section rendered in `get_instructions`

## 2026-04-23 update — incremental scope authorization guide

Added `_INCREMENTAL_AUTH_GUIDE` constant and wired it into the
`stage=="completed"` branch of `get_instructions`. Motivated by the
demo_user_v1 prod incident 2026-04-22 where the agent minted 6
separate `auth login --scope X --no-wait` URLs inside 13 minutes
without ever polling the device_code from any of them.

Why a new prompt block instead of reworking state/flow: the CLI
primitives (`--no-wait` + `--device-code`) already support the correct
two-step flow. What was missing was the agent-side discipline to
(a) poll with the previous turn's device_code instead of re-minting,
and (b) not poll inside the same turn as the mint. Both were absent
from `_IDENTITY_GUIDE`'s one-line missing_scope bullet. The new guide
explicitly scripts Step 1 (this turn: mint, send URL, stop), Step 2
(next turn: poll with the prior device_code, retry original command),
the "do not mint while a URL is in flight" rule, and the
`authorization_pending` error translation. Pins the two-turn
boundary into the prompt so future LLMs / prompt edits can't regress
it silently; the pinning is enforced by
`tests/lark_module/test_incremental_auth_guide.py`.

Gated on `stage == "completed"` for the same reason `_IDENTITY_GUIDE`
is — during onboarding the three-click flow handles authorization
end-to-end and this guidance would be confusing noise.

The `lark_cli` tool docstring in `_lark_mcp_tools.py` was updated to
point at this section rather than restate the incomplete one-liner.

## 2026-04-22 update — C-mini redesign (three-click authorization)

The `get_instructions` render and `hook_data_gathering` were both reworked
as part of the Lark three-click authorization redesign. See
`reference/self_notebook/specs/2026-04-22-lark-three-click-auth-design.md`
for the full rationale.

### What changed

- **Matrix reduced** from 5 binary rows + big narrative block to 3 core
  rows (App / Permissions / Real-time receive) + 1 optional row
  (Visibility). Permission row renders a single `stage` string
  (`not_started` | `waiting_admin` | `waiting_user_click` | `completed`)
  produced by `LarkCredential.current_click_stage()`.
- **Three-click background section** (`_THREE_CLICK_BACKGROUND`) prepended
  to the matrix during configuration, dropped once `stage=completed`.
  This is the ONLY place the Agent learns about the enterprise-tenant
  three-click flow — upstream `lark-shared` SKILL.md is out of our
  control and describes a single-click model. By not touching SKILL.md
  and keeping the correct model inline in `get_instructions`, we win
  on every rendered turn.
- **Coach section** is now strict `stage → single tool call` mapping.
  Every branch is gated on DB state, never on user's literal words
  ("done / 完成了 / 点了" can mean any of Click 1/2/3 — ambiguous by
  design). The branch for `waiting_admin` even spells out
  "if user said 点了 without mentioning admin, still WAIT."
- **Iron rules condensed 16 → 6** (`_IRON_RULES` constant). Deleted
  duplicates (multiple MCP-only restatements, chained-injection variants,
  default-`--as bot` repetition) and literal-word triggers
  ("when user says 'done' → call lark_auth_complete") which are now
  handled inside `lark_permission_advance`.
- **Skill section** (`_build_skill_section`) renders ONLY when
  `stage == completed`. Saves ~600 tokens during configuration where
  the Agent shouldn't be learning `im +messages-send` syntax yet.
  It also carries the "Lark skill files live in the MCP container, not
  your workspace — use `lark_skill(agent_id, name, path)`, never
  `Read`/`Glob`/`Grep`" rule. This rule must be surfaced in all three
  places that teach the Agent about lark_skill (docstring in
  `_lark_mcp_tools.py`; banner prepended by `_lark_skill_loader.py`;
  this system prompt section). Drift in any one undermines the other
  two — see `2026-04-22` post-C-mini link-rewrite change in
  `_lark_skill_loader.md`.
- **P4 fix in `hook_data_gathering`**: removed the `if cred and cred.is_active`
  gate. Now injects `lark_info` for ANY credential row (including
  `pending_setup` / `is_active=False`) so the Matrix can show
  `⏳ creating` during the 15s window between `lark_setup` return and
  `_finalize_setup` completion. Without this fix, Agent would see
  "No Lark bot bound" during that window and try `lark_setup` again
  (which errors with already-exists).
- **`lark_info` schema simplified**: fields `user_oauth_ok`,
  `console_setup_ok`, `bot_scopes_confirmed`, `pending_oauth_url`,
  `pending_oauth_device_code` removed. Single new field `stage` replaces
  them all. `is_owner_interacting` / `current_sender_id` /
  `owner_open_id` / `owner_name` / `receive_enabled` /
  `availability_confirmed` retained.

### Token budget (estimated)

- Unbound: ~500 tokens (mostly iron rules)
- Configuring: ~900 tokens (includes three-click background + matrix + coach)
- Fully configured: ~1200 tokens (swap background for skill section)

## Why it exists

Entry point for the Lark/Feishu integration. Registers the module with
the framework, creates the MCP server, injects Lark credential info into
the agent's context via `hook_data_gathering`, and registers a channel
sender so other modules can send Lark messages on behalf of an agent.

## Design decisions

- **`module_type = "capability"`** — auto-loaded for every agent; no
  LLM judgment needed to activate. The module contributes context and
  MCP tools regardless of whether a bot is bound.
- **MCP port 7830** — chosen to avoid collision with MessageBusModule
  (7820) and earlier modules (7801-7806).
- **`ChannelSenderRegistry.register("lark", ...)`** — class-level
  `_sender_registered` flag ensures the sender is registered exactly
  once across all LarkModule instances.
- **`get_config()` is `@staticmethod`** — matches the framework contract
  where `MODULE_MAP` may call it without an instance.
- **Static instruction fragments as module-level constants**
  (`_NO_BOT_INSTRUCTION`, `_THREE_CLICK_BACKGROUND`, `_IRON_RULES`):
  wording stays identical across turns, and cheap f-string concatenation
  lets `get_instructions` focus on state → section routing only.

## Upstream / downstream

- **Upstream**: `module/__init__.py` (MODULE_MAP), `module_service.py`.
- **Downstream**: `_lark_mcp_tools.py` (tool registration),
  `_lark_credential_manager.py` (`current_click_stage` drives matrix;
  `hook_data_gathering` reads `permission_state`),
  `ChannelSenderRegistry` (send function),
  `_lark_skill_loader.py` (`get_available_skills` inside
  `_build_skill_section`).

## Gotchas

- `hook_after_event_execution` compares `str(ws)` against
  `WorkingSource.LARK.value` because `working_source` may arrive as
  either the enum or its string representation.
- `_build_skill_section` swallows all exceptions in `get_available_skills`
  — a broken skill loader must not crash instruction rendering.
- The instruction string concatenates fragments without Markdown separators
  between them; verify rendering on a live turn if the format looks off
  (some fragments end with a blank line, some don't).
- Token budget is tight — adding any new always-rendered block must
  trade off against something in `_IRON_RULES` or the matrix, not just
  piled on top.
