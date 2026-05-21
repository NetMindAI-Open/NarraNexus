---
code_file: src/xyz_agent_context/module/common_tools_module/common_tools_module.py
last_verified: 2026-05-21
stub: false
---

## 2026-05-21 — artifact 段强化：复杂信息默认 HTML + 可见性边界

`COMMON_TOOLS_INSTRUCTIONS` 的 `#### Visual Artifacts` 段两处加强：
1. **默认优先 HTML artifact**：把"whenever a chart/table would help"升级为
   "**Default to an HTML artifact whenever the information is complex, long,
   or structured**" —— 能用 HTML 表达的（报告 / 仪表盘 / 对比 / 文档 /
   多段答复）几乎都比一大段 chat 文本清晰，拿不准就写 HTML 注册。
2. **可见性边界（新增段 "Where artifacts are visible"）**：artifact tab 只在
   owner 的 web/桌面 chat UI 里；通过 IM 通道（lark / slack / telegram）跟
   agent 交互的人**看不到** artifact，只看到 channel 回复。所以 artifact 永远
   值得给 owner 建，但**不能替代**对 IM 发送者的 channel 回复——给 IM 发送者
   的实质内容要放进 channel message 本身。
配套：[[prompts.py]]（chat_module）也加了同一条指引，因为 artifact 是
owner-facing 可视内容、只有 web chat 能看到。owner-facing「该不该说话 /
说给谁」的纪律仍归 chat_module，本 module 只负责 artifact 能力本身。

## 2026-05-15 — live artifact registry in data gathering + refresh-signal docs

Two coupled additions:

1. `get_instructions` now appends a **"Your registered artifacts"** block
   built from `ArtifactRepository.list_pinned(agent_id)` — the agent sees
   id / kind / title / workspace-relative path of every pinned artifact
   live RIGHT NOW. (The `{agent_id}_{user_id}/` prefix is stripped from
   the DB path because the agent thinks in workspace-relative terms.)
   Empty list still renders a "(none registered yet)" line so the agent
   knows the block is present and trustworthy. Implementation lives in
   `_render_artifact_state_block`; DB errors degrade silently (best-effort).

2. The static instruction explains the **refresh-signal pattern**: after
   registering, the agent can edit the file(s) freely, but the frontend
   doesn't auto-reload — to make the user see the update, call
   `register_artifact` again with `target_artifact_id=<existing id>`.
   That second call IS the refresh signal. Paired with the per-turn
   artifact-list block, the agent always knows which ids are live and
   can target them.

## 2026-05-14-r3 — instruction softened: sibling-assets is a capability, not a rule

The "Always nest the entry inside a fresh, dedicated subdirectory" block
with its two non-negotiable reasons is gone. Deletion is now registry-only
(workspace files are never touched) and the public-raw route degrades to
single-file serving at the workspace root — so there is no class of bug
the rule was protecting against anymore. The instruction now says: "for a
multi-file artifact, use a subdirectory so siblings resolve; single-file
artifacts can sit anywhere". The agent's mental model becomes about
capability, not compliance.

## 2026-05-14-r2 — explicit *why* for "no workspace-root entry"

The "dedicated subdirectory" rule was stated as a preference. The instruction
now spells out the two enforcement reasons:
1. workspace-root entry → whole workspace served → exposes every other file;
   plus `delete_source=true` would wipe the workspace;
2. one folder per artifact → unambiguous deletion + non-overlapping served roots.

Pairs with the parallel update in [[artifact_tool.py]] description.

## 2026-05-14 — artifact instruction rewritten for the pointer model

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

The "Visual Artifacts" section of the module instruction was rewritten:
- one tool now: `register_artifact` (replaces `create_artifact` +
  `upload_artifact_file`);
- the **two-step** mental model is the headline — write files into a
  dedicated workspace subdirectory, then `register_artifact` with the entry
  path; an entry HTML may reference sibling assets (./style.css, etc.) that
  are all served as part of the artifact;
- emphasises "files you write are invisible until you register them" so the
  agent doesn't expect a UI side-effect from `Write` alone.

# common_tools_module.py

## Why it exists

Always-on capability module that hosts generic, domain-agnostic tools
every agent benefits from. Today it owns:

1. `web_search` MCP tool (DuckDuckGo or Brave depending on env)
2. `create_artifact` + `upload_artifact_file` MCP tools for emitting charts,
   reports, CSV, markdown, and interactive HTML to side-panel tabs
3. The system-prompt instruction block that tells the agent how to use
   the **built-in** `Read` tool to view user-uploaded attachments
4. The system-prompt instruction block that tells the agent how to use
   `create_artifact` / `upload_artifact_file` and when to use each kind

Note: there is no MCP tool for attachments — Anthropic's SDK ships the
multimodal `Read` primitive, and our marker text + dynamic instruction
hand it absolute paths. Adding a `load_image` would only confuse the
agent (two ways to do the same thing).

## Upstream / Downstream

Upstream:
- `xyz_agent_context.module.__init__.MODULE_MAP` registers the module
- `xyz_agent_context.module.module_runner` instantiates the MCP server
  on port 7807 (single shared process for all agents)
- `xyz_agent_context.context_runtime.context_runtime` calls
  `get_instructions(ctx_data)` per turn when assembling the system prompt

Downstream:
- `_common_tools_mcp_tools.create_common_tools_mcp_server` creates the
  FastMCP server (`web_search` only)
- `xyz_agent_context.utils.attachment_storage
  .format_attachments_for_system_prompt` renders the current-turn
  attachment block

## 2026-05-14 — `#### Visual Artifacts` instruction rewrite

The artifact block in `COMMON_TOOLS_INSTRUCTIONS` was rewritten:

- **Leads with proactive-use philosophy** — artifacts render at full
  fidelity and look great; the agent should create them *directly* as part
  of the response, not as an announced extra step. (Framed as "just create
  it", deliberately NOT as "don't ask the user" — the latter could bleed
  into other behaviour.)
- **"Pass content inline"** — explicit "don't write the HTML/JSON to a
  workspace file first and then create_artifact from it" rule; doing so
  makes the agent generate the same content twice.
- **Defers the precise contract** (exact `kind` values, params) to each
  tool's own `description=` in `[[artifact_tool.py]]` — the old block
  duplicated it AND carried a wrong signature (omitted the required
  `agent_id` / `user_id` params). One source of truth now.
- **Dropped "no network"** — it was factually wrong. `HtmlRenderer`'s
  iframe is `sandbox="allow-scripts"` with no CSP, so CDN assets (web
  fonts, CSS) DO load. New wording: CDN assets are fine, but embed your
  data in the page — don't fetch it at runtime.
- Mentions the error → fix → retry contract so the agent knows a failed
  call is non-blocking.

## Design decisions

**`get_instructions` is dynamic, not a static string.** The base
description is constant, but the per-turn appendix depends on whether
the user uploaded files in this run. We read
`ctx_data.extra_data["attachments"]` (populated by the trigger layer)
and append a `## Files attached to the current message` block listing
absolute paths. The marker in chat history says the same thing again
at the user-message level — double reinforcement so the model can't
miss it.

**Capability-only, no instance state.** The module never queries the
DB, never looks at the user's history. All behavior depends on the
current turn's `ctx_data`. This keeps it cheap to call on every turn
and side-effect-free.

## Gotchas

- The static instruction text talks about the `Read` tool by name. If
  Anthropic ever renames it (unlikely), the prompt will reference a
  ghost. We've accepted this coupling — it's a single string, easy to
  update.
- `self.user_id` can be `None` for some triggers (background jobs); we
  pass `self.user_id or ""` to the storage helper, which then resolves
  to "no workspace" — appendix is empty in that case (correct: no
  user, no attachments).

## New-joiner traps

- Adding a new generic tool here is fine; adding a tool that scopes
  per-agent is NOT — the MCP server is shared, not per-agent.
- Do not add an attachment-specific tool. The whole rewrite that
  removed AttachmentModule was specifically to avoid that — `Read`
  already handles it.
