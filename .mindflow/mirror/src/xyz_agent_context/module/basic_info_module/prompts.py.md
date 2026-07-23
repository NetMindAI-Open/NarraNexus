---
code_file: src/xyz_agent_context/module/basic_info_module/prompts.py
last_verified: 2026-07-21
---

## 2026-07-10 — Product Feedback Duty 段

narrative 工具指引之后新增 feedback 职责段：两个触发条件（用户表达不满 /
同一指令连续失败 ≥2 次）、摘要一句话且禁止引用用户原文或含 PII、提交后继续
干活不向用户宣布。与 [[_basic_info_mcp_tools.py]] 的 submit_feedback 工具
描述保持同一措辞口径。


## 2026-07-10 — "LLM Model" 行改渲染真实 framework + model

模板里的 `Your LLM model: **{agent_info_model_type}** ({model_name}).` 段保持不变，
但两个占位符的**来源**变了：此前由 [[context_runtime.py]] 写死成
"Claude Agent SDK / sonnet-4"（所有 agent 都自称 Claude Sonnet-4，违反铁律#9），
现在由 [[basic_info_module.py]] `hook_data_gathering` 经 [[agent_model_identity.py]]
按 agent 真实 slot 填（如 "Codex CLI (gpt-5)"）。占位符名没动，故模板文本与文档
注释（131-132）无需改。

## 2026-06-16 — re-surface machine IDs (account vs user_id), fixing register_artifact

The 2026-06-12 change (below) removed `{user_id}` from the identity block so
people render by human name. Side effect burned in production: the agent could
no longer **see** its own `user_id`, so when an MCP tool argument is literally
named `user_id` (e.g. `register_artifact`, `create_narrative`) the LLM
substituted the only user-identity string still visible — the **display name**,
which for NetMind users equals their **email** (`get_display_name` returns
`display_name or user_id`, and NetMind sets `display_name = email`). With
`user_id=<email>`, [[registration.py]] `_resolve_entry` computes a
nonexistent workspace `{{base}}/{{agent_id}}_<email>` → every relative path is
rejected "does not point at an existing file" and every correct absolute path is
rejected "outside your agent workspace". Root cause traced from prod agent
`agent_e4233ac4068f` (DM-game agent) on 2026-06-16; a cron-driven sibling
(`agent_9bbb5f409b3e`) succeeded only because it copied the full absolute
workspace path (with the hex id) it observed via bash cwd.

Fix: the **Your Identity** and **Current Session** blocks now render both
`Agent ID: {{agent_id}}` and `User ID: {{user_id}}` as backticked stable IDs,
with an explicit "Account vs. ID — never confuse the two" note: the human
account (email / login / display name shown under "Talking with") is for
conversation only and is never a valid `agent_id` / `user_id` tool-argument
value. This is the machine-identity half that 2026-06-12 dropped; human names
are still shown for conversation, so both pieces now coexist, clearly labelled.
`{{user_id}}` is a top-level [[context_schema.py]] field, so it renders without
new plumbing.

## 2026-06-12 — identity lines render people by human name

`BASIC_INFO_MODULE_INSTRUCTIONS` no longer prints `{creator_id}` / `{user_id}`
as the agent's owner / counterpart. The identity block now reads
`Creator (your owner): {creator_name}`, `Is the current speaker your Creator?:
{is_creator}`, and `Talking with: {current_speaker_name}` — all human names
resolved in [[basic_info_module.py]] `hook_data_gathering` via
[[user_repository.py]] `get_display_name`. The opaque NetMind userSystemCode is
no longer shown as a person. New placeholders require the matching
[[context_schema.py]] fields (creator_name / is_creator / current_speaker_name)
or `.format()` raises KeyError.

## 2026-05-20 (Fix #2 P3) — narrative-tools section added to instructions

BASIC_INFO_MODULE_INSTRUCTIONS gained a "Conversation threads (narratives) & your
narrative tools" section: explains the unified timeline tags
`[time · topic · nar=… · evt=…]`, the recent-activity list, and when/how to use
view_narrative / view_event / switch_narrative / create_narrative — including the
key heuristic that a short reply usually continues the MOST RECENT line, and to
switch/create only when confident the default thread is wrong. Tools live in
[[_basic_info_mcp_tools.py]].
## 2026-04-23 — 新增 "Working Memory Across Turns" 段

`BASIC_INFO_MODULE_INSTRUCTIONS` 在 Runtime Environment 段**前**新增一个
"Working Memory Across Turns" 说明段。告诉 Agent 两件事：

1. 它的 reasoning（tool call 之外写的文字）**跨 turn 保留**；
2. tool call 的 arguments 和 outputs **单 turn 后消失**，下一轮看不到。

配套要求：当 tool 结果里有 Agent 下一轮需要用的值（device_code、job_id、
刚建的 url、file token、session id 等），必须在 ending turn 之前把那个值
**明文 restate 到自己的 reasoning 里**。附了一段 Lark 增量授权的 concrete
example 演示正确动作。

**为什么放在 BasicInfo 而不是 ChatModule**：这条规则对所有 trigger source
都适用（Chat / Lark / Job / Bus / A2A / Callback / Skill），不是对话场景
专属。BasicInfo 是每个 Agent run 都加载的 always-on 模块，最合适。

**Curly-brace escaping gotcha**：`BASIC_INFO_MODULE_INSTRUCTIONS` 是
`str.format(**ctx)` 渲染模板，`{key}` 被当占位符。示例里出现
`{device_code: ABC…}` 或 JSON 示例都必须双写 `{{...}}`。遗忘会导致
`KeyError: 'device_code'` 抛在 `get_instructions()` 里——首次部署这个修改
时就踩过这个坑，被 `tests/basic_info_module/test_deployment_context.py`
的 integration 测试兜住了。

---

# prompts.py — BasicInfoModule 指令定义

## 为什么存在

`BASIC_INFO_MODULE_INSTRUCTIONS` 向 Agent 注入运行时的基础环境信息：当前时间、Agent ID、用户 ID 等。这让 Agent 在回答"你是谁"或使用工具时有正确的自我认知，不需要猜测或要求用户提供这些信息。

## 上下游关系

- **被谁用**：`BasicInfoModule.__init__` 赋值给 `self.instructions`；`XYZBaseModule.get_instructions()` 用 `ctx_data` 字段格式化后注入系统提示
- **依赖谁**：无外部依赖，纯文本常量；占位符由 `ContextData` 字段提供（如 `{agent_id}`、`{user_id}`、`{current_time}`）

## 设计决策

BasicInfoModule 的 prompts 是最稳定的 prompt 文件之一——它只描述客观事实（谁、何时、在哪运行），不含业务规则或行为约束。修改它的唯一理由是 `ContextData` 的字段变化。

## 新人易踩的坑

- `ContextData` 里字段名变更时，记得同步更新这里的占位符，否则 `get_instructions()` 的 `.format()` 会在运行时抛 `KeyError`。这类错误只在 Agent 实际被调用时才会暴露，不会在 import 时报错。
