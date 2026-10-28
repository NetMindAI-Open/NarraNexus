---
code_file: plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/_common_tools_impl/artifact_tool.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 私有平台模块换成公开门面（批 6c，A2-1）

本文件曾 import `narranexus.platform` 的下划线私有模块。批 6c 在拥有它的包上开出了
具名公开函数（`module_system` 的 caller-identity 解析器 / `marketplace` 的
store·pipeline·secret-box / `agent_framework.llm.prompt_probe_emit` /
`agent_framework.adapters.build_tool_policy_guard`），本文件改用它们。
理由不是命名规范：这些 MCP 工具和 helper 自批 6b 起是**独立的 wheel**，
「包内私有」对它们已经不成立了。`pyproject.toml` 的
`plugins never import a private platform module` 契约（只查直接 import）守住这条线。

## 2026-08-10 (review 修正) — 两段落点指令互斥，已统一

多文件那段原本写「写进**你 workspace** 的专用子目录」，而团队那段写「写进**团队共享目录**」——
两条对同一个 agent 同时成立时是矛盾的，且团队分支缺了「多文件要建子目录」这句。

现在多文件那段只讲**要建子目录**这件事本身，把「哪个顶层」交给下一段的归属规则：私有 → 自己
workspace，团队 → 团队目录，**多文件的团队 artifact 因此进团队目录的子目录**。

这也顺带压低了 [[registration.py]] 记的那个体量陷阱的暴露面：entry 在子目录里时，
`artifact_root` 就不再是团队根。

## 2026-08-10 (方案 B) — 工具描述点明团队里往哪写

新增一段：**团队房间里把文件写进团队共享目录再注册**，不要写自己 workspace——后者对队友的工具
不可达，从那里注册的 artifact 没人能接着做。并说明从自己 workspace 注册团队 artifact 会被拒、
错误里带目录路径。规则本身见 [[registration.py]]。

## 2026-08-07 — scope 参数：team 由服务端定，模型只有否决权

新增 `scope: str = "auto"`。**非对称设计**，这是本次的关键：

- **team 来自服务端** `caller_team_id_from_request()`。模型**无法**通过传参把 artifact 放进
  某个 team——`agent_id` 本身就是模型填的参数，若 team 也信参数，私聊回合就能写进团队空间。
- **`scope="private"` 是模型唯一的杠杆，且只能收窄**：把草稿从「它确实所在的 team」里摘出来，
  永远不能用来够到一个它不在的 team。
- **其他任何值（含默认值与模型瞎编的值）一律跟随 turn**。这是弱模型下的安全方向：常见情况
  零决策，编错了退化成**正确行为**，而不是退化成「一个团队里谁都找不到的私有 artifact」。

**参数必须是 `str` + 默认值，不能是 `Optional[str]`**：FastMCP 会把 Optional 渲染成
`anyOf:[X,null]`，strict-schema provider 直接**请求级 400**——整个请求挂掉，不是这一次调用
降级。测试 `test_scope_parameter_is_not_optional_typed` 守着这条。

⚠️ 本文件顶部的前端耦合警告（`ARTIFACT_TOOL_BASE_NAMES`）仍然有效：本次**没有改工具名**，
只加参数，故前端无需同步。

## 2026-07-22 — new `open_url` MCP tool

Second tool registered alongside `register_artifact`: `open_url(url, title?)`
lets the agent open a web page as a URL tab, delegating to
`ArtifactService.open_url`. Same `{error, code}` failure contract. The tool
name is NOT matched by the frontend live-discovery constant (that is
`register_artifact` only) — URL tabs surface via the normal artifact refresh,
so no ChatPanel coupling to keep in sync here.
## 2026-05-20 — quota wording purged (v1.7.0 removal cleanup)

The per-user artifact quota was removed in v1.7.0 (see [[artifact_runner.py]]),
but stale "quota" wording survived in this tool's LLM-facing `description=` and
in the body docstring/comment. Removed: the description's error-cause list no
longer says "quota" (only "path outside workspace, file missing, too large"),
and the `ArtifactQuotaExceeded` exception / `code === 507` modal references
below are historical only — neither exists anymore. Keeping the stale wording
risked the agent self-limiting or telling users about a quota that's gone.

## 2026-05-15 — description teaches the refresh-signal pattern

The tool description now spells out the **re-register-as-refresh-signal**
contract: after the first register, the agent can keep editing the
workspace file(s) freely, but the frontend doesn't auto-reload. To make
the user see the update, call `register_artifact` again with
`target_artifact_id=<existing id>` — that second call is what the
frontend listens for to refetch the entry HTML and any sibling assets.
The description also points the agent at the per-turn "Your registered
artifacts" system-prompt block so it knows which ids are currently live.

## 2026-05-14-r3 — hard rule dropped; sibling-assets reframed as a capability

The "must sit in a subdirectory, NOT directly in the workspace root"
non-negotiable rule is gone. With `delete_source` removed (deletion is now
registry-only) and the serving route soft-degrading at the workspace root
(only the entry serves), the rule had nothing left to enforce.

The description now phrases the subdirectory requirement as a **capability
hint**: "for a multi-file artifact, put the files in a dedicated
subdirectory so the entry's relative references resolve; single-file
artifacts can sit anywhere including the workspace root". The agent gets a
clean mental model — there are no register-time gotchas, the worst thing
that can go wrong is "my entry HTML references ./style.css and gets 404
because I put it at the workspace root", which is an immediate, fixable
feedback loop.

The catch-all error contract still mentions the cause categories ("path
outside workspace, file missing, too large, quota") so the agent reads the
error and retries.

## 2026-05-14-r2 — tool description spells out *why* workspace root is rejected

The earlier description told the agent "must sit in a subdirectory of your
workspace, not directly in the workspace root" but only implied the reason
("entry file's folder becomes the artifact"). An agent could read that as a
stylistic preference. The description now explicitly states the two
consequences of an entry at the workspace root:
- the whole workspace is served wholesale → every other file (Bootstrap.md,
  other artifacts, drafts) is exposed via the public token URL;
- a later `delete_source=true` would rmtree the entire workspace.

Same explanation is now in the module instruction (see
[[common_tools_module.py]]). The runtime error message already mentioned
"that whole folder becomes the artifact" — kept as-is for terseness.

## 2026-05-14 — collapsed to one `register_artifact` tool (pointer model)

The two old tools — `create_artifact` (inline content) and
`upload_artifact_file` (copy a workspace file) — are replaced by **one**
`register_artifact(entry_path, kind, title, ...)`. The new tool registers a
*pointer* to a file the agent already wrote in its workspace; it never copies,
moves, or writes content.

The description was rewritten around the core mental model the agent must hold:
**files you write are invisible until you register them**; the tool only
registers a pointer; put each artifact in its own dedicated subdirectory so an
entry HTML can reference sibling assets. Catch-all `except Exception` → `{error,
code: 500}` is preserved.

## 2026-07-21 — delegates to ArtifactService

The registration logic moved out of this module's `_common_tools_impl` into
the dedicated `narranexus/platform/artifact/` package. The tool now delegates to
`ArtifactService.register` and imports the `ArtifactError` hierarchy from
`narranexus.platform.artifact` — same validation, same error contract; only the
seam changed. Older entries below reference `artifact_runner`, its previous
home.

## Why it exists

Bridges the artifact subsystem's validation + DB logic into an LLM-callable
MCP tool. Each call: resolve the per-loop DB client → construct
`ArtifactService` → delegate to `service.register` → catch structured
exceptions → return an LLM-readable dict.

## Why it lives in common_tools_module, not a dedicated ArtifactModule

Artifact registration is a **capability** tool the LLM may reach for in any
session — not a scenario-specific Module. A dedicated Module would cost an extra
MCP port, process, and prompt-engineering surface; common_tools already has the
full MCP server infrastructure (factory, timeout decorator, backend dispatch).

## Upstream / Downstream

- **Called by**: `_common_tools_mcp_tools.py` → `create_common_tools_mcp_server`
  calls `artifact_tool.register(mcp)`.
- **Depends on**: `narranexus.platform.artifact.ArtifactService` (validation +
  DB orchestration), `get_db_client` (per-loop DB singleton).
- Does **not** use `with_mcp_timeout` — registration is local I/O (path stat +
  one DB write), no external network call to guard.

## The registered tool — `register_artifact`

- Purpose: surface a workspace file (or multi-file folder via an entry HTML) as
  a visual tab.
- Params: `entry_path` (absolute or workspace-relative, anywhere inside the
  workspace — a dedicated subdirectory enables sibling assets, the workspace
  root gives single-file mode), `kind`, `title`, `target_artifact_id`
  (optional, update-in-place).
- Returns `CreateArtifactToolResult.model_dump(mode="json")` → `{artifact_id,
  url, created_at}`. The description tells the LLM not to repeat the URL in its
  reply (the tab is already visible).

## Error handling

Two layers:
1. `ArtifactError` (+ subclasses `ArtifactTooLarge`, `ArtifactNotFound`,
   `ArtifactKindMismatch`, `ArtifactPathEscape`) →
   `{"error": str(e), "code": e.code}`. Expected, structured, actionable.
2. Catch-all `except Exception` → `{"error": "... likely transient — you can
   call the tool again", "code": 500}`. FastMCP swallows unhandled exceptions
   into opaque MCP errors that can stall the agent loop; the catch-all
   guarantees every failure is a structured, retryable dict.

The `{error, code}` shape is a stable contract — the agent reads it to
self-correct and retry. Don't change the shape.

## Gotchas

- ⚠️ **Frontend coupling**: the tool name `register_artifact` is matched by
  `ARTIFACT_TOOL_BASE_NAMES` in `ChatPanel.tsx` for live artifact discovery.
  Renaming the tool requires updating that constant in the same change.
- `get_db_client()` returns a process-level singleton — `await` it per call,
  don't cache.
- The handler `register_artifact` is a closure inside `register(mcp)` captured
  by the FastMCP decorator — don't lift it to module scope (breaks the
  `register(mcp)` encapsulation pattern shared with the web_search tools).
- `kind` is typed `str` in the MCP schema but the service expects the
  `ArtifactKind` Literal — `# type: ignore[arg-type]`; runtime validation is in
  the registration impl.

## 2026-08-18(二)— 前端耦合注释降级为 display-only

工具名匹配不再驱动发现(事件化后),只锚定聊天内徽章;改名的代价从「tab 静默
消失」降为「旧转写少个 chip」。

## 2026-08-18 — list_artifacts 工具

只读库存工具:可见面=list_for_agent_context(成员关系服务端推导,**参数只能收窄
不能扩大**——报不属于自己的 team_id 得到空集,构造上安全);kind/team_id/
title_contains 过滤,50/页。impl 为纯函数(直接测试),@mcp.tool 薄包装。
可选参数用 str=""(FastMCP 把 Optional 渲染成 anyOf null,严格 schema 供应商
整请求 400——scope 参数的旧教训)。**听众隔离预留**:将来按 #309 落时,过滤加在
impl 内、取服务端身份头(caller_team_id_from_request),签名不变。

## 2026-08-20 — 过滤分页下推 SQL(#334 I10);行渲染消费 (id, line) 对
