---
code_file: src/xyz_agent_context/module/basic_info_module/prompts.py
last_verified: 2026-05-20
---

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
