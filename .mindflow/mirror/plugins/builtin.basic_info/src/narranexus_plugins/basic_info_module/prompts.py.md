---
code_file: plugins/builtin.basic_info/src/narranexus_plugins/basic_info_module/prompts.py
last_verified: 2026-09-09
---


## 2026-09-09 — Product Feedback Duty：平台注入凭据被拒成为第 3 触发条件 + 保守措辞

起因（prod 2026-09-09，agent_6b2dc72fc697）：平台代跑的 narra-cli 回
`agent-token-invalid`（真因是平台把 token 送错了后端），agent 一次都没
`submit_feedback`（原只有两个触发：用户不满 / 同指令连败 2 次），反而对用户断言
「平台缓存了过期 token」，并把 token 明文贴进聊天「对比」。

改动（同段内）：
- 触发 3 的范围是**平台注入的**凭据 / 端点 / 额度被平台工具拒绝
  （`agent-token-invalid`、原本能用的绑定突然 401/403）→ `category=error` 上报，
  写明工具名和错误码，重试或绕过成功也要报；**每个 agent 每个 工具+错误码 只报
  一次**，靠 `dedup_key="<tool>:<code>"` 由工具侧执行，不靠模型记忆
  （见 [[_basic_info_mcp_tools.py]]；首版只写在文案里，Opus 评审 I2 打回：触发 3
  是机器生成事件，平台级故障每次工具调用都复现，而长 agent_loop 的上下文会被压缩，
  「我报过了」正是最先被压掉的那类事实）。
- 明确**不算**触发 3：没绑定时的 `no_credential`（十几个渠道工具的正常答复，
  lark 文案甚至教 agent 主动触发它确认干净状态）、`official-agent-required` 这类
  设计内的策略拒绝、bind/setup 工具拒绝用户刚输入的 secret——这些是给用户的答案，
  不是产品缺陷。（首版把前两者写成了触发条件，Opus 预审 C1/C2 打回。）
- 「Be conservative about causes」只管触发 3 类错误：看不到平台怎么发凭据，禁止
  断言诊断；用户自己给的凭据（bind secret、BYOK key）**不适用**，各模块原有的
  明确诊断（discord intent 没开、lark secret 错）照旧。
- 泄密红线（无例外）：绝不把 token / API key / access token / 凭据文件内容贴进
  消息，「证明它有效」「对比两个」都不行。
- 措辞刻意不用「report it」——narramessenger 文案里这词是「告诉用户」的意思。
- **「团队已被通知」不写在 prompt 里**（Owner 定调 2026-09-09）。它不是一条规则，
  而是**那一次 submit_feedback 调用的结果**：`send_feedback` 吞掉所有异常只打
  DEBUG 日志，`NARRANEXUS_FEEDBACK_DISABLED=1` 的部署更是一条都不发——agent 看不见
  这些，写在通用指令里就等于让它对用户断言一件平台可能根本没做的事（和本段要修的
  「别断言你验证不了的原因」是同一个毛病）。所以文案只留「读 submit_feedback 的结果、
  只转述它说的话，绝不自作主张说团队已被通知」，真话由
  [[_basic_info_mcp_tools.py]] 的 `_feedback_result` 按 `delivered` 给出。
  这同时从根上解掉了 Opus 评审 I1 那对互相对撞的祈使句：Rules 段「别宣告」保持默认，
  只留**一个**出口——工具结果本身；不要反过来删 Rules 段那句，它约束的是触发 1/2。
- Rules 段的「never include names」指的是人名 / PII，与十行外「写明工具名」冲突；
  已消歧为 personal names + 明说工具名和错误码是必填、不是 secret。弱模型（铁律 #15）
  否则会保守地交出「一个平台凭据被拒了」这种无法定位的 summary。
- 触发 2 与触发 3 在同一次失败上会同时命中（平台凭据被拒导致同一指令连败 2 次），
  段内写死优先级：按触发 3 记 `error`，不重复记两条。
与 [[_basic_info_mcp_tools.py]] 的工具描述 (c) 条同口径（含 dedup_key、(b)/(c)
优先级与排除项）。测试 `tests/basic_info_module/test_feedback_duty_platform_errors.py`
对 legacy 与 STABLE 两份模板都钉住触发、排除项、作用域、「不许自行宣告已通知」与
红线；回退任一文件即红。切片守卫按下一个 `#### ` 标题取段（不再按第一个 `---`），否则段内以后加
一条横线就会把守卫悄悄缩小到前半段。

## 2026-08-18 — 新增「Time-bound Commitments」段

agent 说"我周五通知你"这件事，在 runtime 眼里等于什么都没说：没有任何机制会在那个时刻
重读它的回复。承诺静默过期，而从用户那侧看，这跟被无视没有区别。

所以加了一条硬规则：带时间点的承诺必须当场排程（`job_create`），时刻用
`resolve_relative_date` 算而不是自己推，事后动手前用 `compare_dates` 复核。

**为什么放在 BasicInfoModule 而不是 JobModule**：JobModule 是 `module_type="task"`，不一定
被加载 —— 而它没被加载的那些轮，恰恰是 agent 最可能随口做出承诺的时候。BasicInfoModule
是 capability，永远在，而且它本来就管时间真值。

放在 `BASIC_INFO_REAL_WORLD_TURN_TEMPLATE` 这个 volatile span **之外**：这段是静态文本，
属于可缓存前缀。写进 span 里会让它每轮都随 turn context 重发一遍，永久多付，而且从系统
提示里消失。`tests/basic_info_module/test_time_bound_commitments.py` 锁了这条。

## 2026-07-28 — R4b：{current_time} 段搬迁出模板（turn-context relocation）

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

`BASIC_INFO_MODULE_INSTRUCTIONS`（legacy，一字未动）之外新增三个常量：

- `BASIC_INFO_REAL_WORLD_TURN_TEMPLATE` — "Real World Information" 小节
  （含 `{current_time}` 与 ground-truth 反幻觉指引），**必须与 legacy 模板内
  对应 span 逐字节相同**（stable 版靠 `.replace()` 用它做锚点推导；
  `tests/module/test_turn_context_split.py` 锁定锚点存在性）。
- `_REAL_WORLD_STABLE_SECTION` — 静态指引句（时间在本轮消息 turn context 块里）。
- `BASIC_INFO_MODULE_INSTRUCTIONS_STABLE` — legacy 模板 replace 推导出的
  字节稳定版，供 relocation flag 开启时使用。

动机：`{current_time}` 秒级易变是 BasicInfo 在 prod SYSPROMPT-BREAKDOWN 中
0/17 稳定的根因；搬到 turn context 后 system prompt 前缀可缓存。speaker 相关
占位符（current_speaker_name/is_creator/user_role）**留在两个模板里**——换人
打穿一次是合法语义。修改这个小节的措辞时必须同步改 legacy 模板与 turn
template 两处，否则 replace 静默失效（有测试兜底）。

## 2026-07-10 — Product Feedback Duty 段

narrative 工具指引之后新增 feedback 职责段：两个触发条件（用户表达不满 /
同一指令连续失败 ≥2 次）、摘要一句话且禁止引用用户原文或含 PII、提交后继续
干活不向用户宣布。与 [[_basic_info_mcp_tools.py]] 的 submit_feedback 工具
描述保持同一措辞口径。


## 2026-07-10 — "LLM Model" 行改渲染真实 framework + model

模板里的 `Your LLM model: **{agent_info_model_type}** ({model_name}).` 段保持不变，
但两个占位符的**来源**变了：此前由 [[context_runtime.py]] 写死成
"Claude Agent SDK / sonnet-4"（所有 agent 都自称 Claude Sonnet-4，违反铁律#9），
现在由 [[basic_info_module.py]] `gather` 经 [[providers/model_identity.py]]
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
resolved in [[basic_info_module.py]] `gather` via
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
`KeyError: 'device_code'` 抛在 `contribute_instructions()` 里——首次部署这个修改
时就踩过这个坑，被 `tests/basic_info_module/test_deployment_context.py`
的 integration 测试兜住了。

---

# prompts.py — BasicInfoModule 指令定义

## 为什么存在

`BASIC_INFO_MODULE_INSTRUCTIONS` 向 Agent 注入运行时的基础环境信息：当前时间、Agent ID、用户 ID 等。这让 Agent 在回答"你是谁"或使用工具时有正确的自我认知，不需要猜测或要求用户提供这些信息。

## 上下游关系

- **被谁用**：`BasicInfoModule.__init__` 赋值给 `self.instructions`；`XYZBaseModule.contribute_instructions()` 用 `ctx_data` 字段格式化后注入系统提示
- **依赖谁**：无外部依赖，纯文本常量；占位符由 `ContextData` 字段提供（如 `{agent_id}`、`{user_id}`、`{current_time}`）

## 设计决策

BasicInfoModule 的 prompts 是最稳定的 prompt 文件之一——它只描述客观事实（谁、何时、在哪运行），不含业务规则或行为约束。修改它的唯一理由是 `ContextData` 的字段变化。

## 新人易踩的坑

- `ContextData` 里字段名变更时，记得同步更新这里的占位符，否则 `contribute_instructions()` 的 `.format()` 会在运行时抛 `KeyError`。这类错误只在 Agent 实际被调用时才会暴露，不会在 import 时报错。


## 2026-08-18 — owner 工具改名跟随

`send_message_to_user_directly` 拆成 `reply_owner`（回答刚说话的 owner）与 `notify_owner`
（未被问就主动告知）。两者行为相同但纪律相反，合成一个工具就要求模型每轮自己判断该用哪种
register。本文件里改到的是该 handler 注册的 `user_reply_tool_names` / 相关文案 —— 一两行，
但 registry 条目是**活的行为**：它决定哪些工具调用算作这个来源的一次回复，也是
`render_origin_declaration` 取 label 的同一条记录。规范解释见
[[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。
