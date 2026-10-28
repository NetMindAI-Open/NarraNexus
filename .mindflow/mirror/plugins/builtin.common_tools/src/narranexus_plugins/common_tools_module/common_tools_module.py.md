---
code_file: plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/common_tools_module.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（PR #421 复审 I1）— office 锁探测只看属主根内的 entry

状态块的 `~$` 锁探测原来 `os.path.join(base_working_path, file_path)` 后直接 `os.path.exists`，
跟随路径上任何链接——artifact 所在目录被换成指向别的租户目录的链接时，会把"对方正开着这份
文档"写进本 agent 的上下文。现在先经公共入口 `ArtifactService.resolve_owned_entry`（不 import
`_artifact_impl`），`ArtifactPathEscape` → 不加这条 note。同一个状态块里的 freshness 检测已在
[[freshness]] 内部做同样的限定。测试
`test_office_lock_probe_does_not_follow_a_link_into_another_tenant`（tests/common_tools_module/）。

## 2026-09-23 — 示例花括号遵循模板契约

产物返回值示例使用双花括号，经过模块统一的 `str.format` 后才呈现为普通花括号。
它不是运行时上下文字段；未转义会让包含此能力的整个回合在组装上下文时失败。
跨模块模板回归测试检查这类代码示例，防止新增浏览器指令重复触发同一故障。

## 2026-09-13 — marker 的 path 是 JSON 字符串

给 agent 的附件说明改为：`path=` 的值是 JSON 字符串。普通路径就是引号里的文字；含转义（`\"`、`\\`、`\t`、`\n`）时要先解码再交给 Read。起因是 `file_marker` 改用无损的 `exact_literal` 编码 path，照字面读带转义的路径会读错文件。

## 2026-09-13（PR#401）— 指令里的附件 marker 形状同步为真实形状

marker 形状现在是 `[User uploaded file: name="…", path="…", mime="…", kind="…"[, transcript="…"] — use Read tool to view]`（`attachment_schema.file_marker`：每个值都是 JSON 字面量，kind 从 head 挪成字段），说明文字跟着改，并提示把 `path` 引号内的文本交给 Read。当前轮附件列表（`attachment_storage.format_attachments_for_system_prompt`）同一不变量：每个值都编码。

## 2026-08-18 — 指令里新增「Dates and Time Arithmetic」段

配合新注册的 `resolve_relative_date` / `compare_dates`（见
[[date_tool.py]]）。两条规则：相对表达一律过工具；断言某事"已经发生 / 就是今天 / 还没到"
之前先用 `compare_dates` 核一遍并引用返回值。

第二条是独立的一条，不是第一条的推论 —— 从记录里读对的日期，仍然可能跟"现在"比错，而
那才是用户真正会注意到的错误。

## 2026-08-10 (review 修正) — 跨 workspace 的条目渲染绝对路径

状态块此前只剥离**本 agent 自己**的 workspace 前缀，不匹配就原样输出 base 相对路径。
队友的团队 artifact 两个前缀都不匹配，于是 agent 拿到一个**相对路径**——而相对路径会按自己
workspace 解析（NexusPower 的 confinement 层刻意如此 rebase），指向一个不存在的文件。

后果直接打在验收 #3 上：「看得见清单」成立，但「接力更新」的第一步（读到队友当前内容）
在渲染层就断了。

现在：自己 workspace → 短相对形式（工具直接可用）；**其他一律绝对**。块尾指令同步说明这一类
是跨 workspace 的，否则 agent 会把每一条都当相对路径读。

## 2026-08-07 — artifact 状态块改用并集查询

`_render_artifact_state_block` 从 `list_pinned`（纯私有）改为 `list_for_agent_context`
（私有 ∪ 所属全部 team）。这个块正是 agent **得知队友产出存在**的唯一途径——继续只列自己的
行，接力更新就会静默失效（不报错、agent 只是再也不提那个 artifact）。上限 20 不变，跨两半
统一按 `updated_at` 倒序。跨 team 边界由 [[artifact_repository.py]] 的成员子查询保证。

## 2026-08-07 — artifact 状态块设上限（ARTIFACT_STATE_BLOCK_LIMIT=20）

`_render_artifact_state_block` 此前列出**全部** pinned artifact、无上限无排序，而
artifact 没有数量配额 → 注册得越多，system prompt 每轮越长，无限增长，且为早已不碰
的条目持续付费。现在取 `list_pinned(limit=20)`（按 updated_at 倒序）。

截断是安全的：该块是便利视图而非事实来源，全量始终可经 artifact 工具取回；配合倒序
后还能自我修正——正在迭代的会因重新注册刷新时间戳而留在列表，掉出去的是真正冷的。

上限值放在模块常量而非 artifact 领域层：约束来自 **prompt 预算**，属于本模块的关切。


## 2026-07-28 — R4b：两个附录搬进 contribute_turn_context

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

`contribute_instructions` 原本 = COMMON_TOOLS_INSTRUCTIONS + 本轮附件附录 + live
artifact registry；后两者每轮/会话中途变（附件随上传、registry 随
register_artifact，prod 稳定性 14/17）。现拆为：

- `_volatile_sections(ctx_data)` — 两个附录的渲染原样提取（附件经
  `format_attachments_for_system_prompt`，registry 经
  `_render_artifact_state_block`，DB 失败返回 "" 的 fail-open 语义不变）。
- `contribute_instructions` — flag 开 → 只返回常量 `self.instructions`（轮间字节
  稳定）；关 → legacy 三段拼接（逐字节一致）。
- `contribute_turn_context` — 两个附录（各自带稳定标题："Files attached to the
  current message" / "Your registered artifacts (live)"）。

附件双渲染（本块 + user message 的 Read marker）保持——flag 开后两处都在
消息侧。`_render_artifact_state_block` 里原先函数内的 `settings` 局部 import
删除（模块级已导入，供 flag 读取）。

## 2026-07-22 — artifact state block points the agent at URL-tab content

`_render_artifact_state_block` special-cases `application/x-url` artifacts:
when the sibling `content.md` (the page's text snapshot from [[url_artifact.py]]
/ [[page_text.py]]) EXISTS on disk, it tells the agent to `Read` it so it can
SEE the page content; legacy tabs opened before this feature (no content.md)
fall back to the plain path — a blind hint would point the agent at a missing
file (PR #137 review). The content filename comes from the schema constant
`URL_TAB_CONTENT_FILENAME`, not a private-impl import.

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

1. `contribute_instructions` now appends a **"Your registered artifacts"** block
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
- `narranexus.platform.module_system.__init__.module_registry` registers the module
- `narranexus.platform.module_system.module_runner` instantiates the MCP server
  on port 7807 (single shared process for all agents)
- `narranexus.platform.context_runtime.context_runtime` calls
  `contribute_instructions(ctx_data)` per turn when assembling the system prompt

Downstream:
- `_common_tools_mcp_tools.create_common_tools_mcp_server` creates the
  FastMCP server (`web_search` only)
- `narranexus.platform.utils.attachment_storage
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

**Static base + per-turn appendices, split by the R4 relocation flag.**
The base description is constant; the per-turn appendix depends on whether
the user uploaded files in this run. We read
`ctx_data.extra_data["attachments"]` (populated by the trigger layer)
and render a `#### Files attached to the current message` block listing
absolute paths. With the relocation flag ON (default) the appendices are
emitted via `contribute_turn_context()` into the current message and
`contribute_instructions` stays byte-constant; flag OFF appends them to the
instruction as before. The marker in chat history says the same thing again
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

## 2026-08-18 — 状态块诚实尾注 + 行渲染抽到 artifact_lines

截断不再静默:count_for_agent_context 单独 COUNT,超过 LIMIT(20)时尾注写明
「还有多少 + 用 list_artifacts 查」——治的是「agent 把窗口当全集,老 artifact 被
认定不存在→重复创建」。COUNT 失败降级为无尾注而非无块。行渲染移入
_common_tools_impl/artifact_lines.py(与 list_artifacts 工具共用)。

## 2026-08-19 — 状态块=外部编辑侦测的 T-A 触发点

渲染时逐 artifact 跑 refresh_external_state(≤上限行,成本恒定),
确认的外部改动当场提交;行标记由 **history 最后动作**导出(无状态):
external_edited→EXTERNALLY MODIFIED 行;user_edited→modified by the
user 行;agent 重注册(updated/registered)自动清标。office kind 另查
~$ 锁附「desktop Office」提示。整段 best-effort——侦测失败只降标记
不降块。

## 2026-08-20 — 侦测循环改有界并发(#334 I12)

串行 await 链改 Semaphore(4)+gather;单行失败仅降该行标记,不再
杀整个 pass。并发只针对不同 artifact_id 行,写侧天然不冲突。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

## 2026-09-04 · declares its platform metadata (batch 5b)

`get_config()` is static and carries what the platform used to table about this module (default / base / always-load membership, instance prefix, role, display, decision metadata as applicable).
