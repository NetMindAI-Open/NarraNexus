---
code_file: src/xyz_agent_context/agent_framework/agent_model_identity.py
last_verified: 2026-07-10
stub: false
---

# agent_model_identity.py — 解析 agent 真实的 (framework, model) 供 prompt 展示

## 为什么存在

系统 prompt 里那行 "Your LLM model: **X** (Y)" 曾经在 [[context_runtime.py]]
里被写死成 "Claude Agent SDK" / "sonnet-4"，于是**每个** agent 不管真实配置都
自称 Claude Sonnet-4，被问"你是什么模型"就照读（还违反铁律#9：身份绑死某框架）。
本模块把真身份从 runtime 实际派发用的 slot 行里解析出来，让 prompt 说真话
（如 codex_cli+gpt5 → "Codex CLI (gpt-5)"）。

放在 agent_framework 层而非 Module 里，是守铁律#9：[[basic_info_module.py]]
（一个 Module）只调 `resolve_agent_model_identity` 并渲染字符串，永远不认识
框架名。

## 设计决策

- **单一 overlay 实现**：本文件是唯一的 overlay，[[step_3_agent_loop.py]] 的
  `_resolve_agent_framework_name` **委托**到这里（`return (await resolve_...).framework`），
  所以 prompt 展示的身份与 dispatch 真正跑的 driver **不可能不一致**。
  夺权规则：per-agent `agent_slots` 覆盖行只有在**同时带 `provider_id` 和
  `agent_framework`** 时才胜出（provider-only 或 framework-only 的残行都不夺权，
  与 config resolver 一致），否则回退 owner（`agents.created_by`）的 `user_slots`。
  framework 和 model **都从同一个胜出的 slot 行读**（两表都有这两列）。
  - **踩过的坑（PR #84 review）**：`agent_slots.agent_framework` 是 `nullable=True`。
    初版判定只看 `provider_id`，漏了 `agent_framework` 非空这一条——于是"有 provider
    但 framework 为 NULL"的行会被本 resolver 当胜出、渲染成 Claude，而 dispatch 端
    落到 owner 框架真跑 Codex，重新制造错误身份。收敛成单一实现后此类不一致从根上消除
    （方向也纠正了：`agent_runtime → agent_framework` 本就是合法 import 方向，step_3
    早已 `from xyz_agent_context.agent_framework import ...`）。
- **绝不抛异常**：任何缺行/空列/DB 故障都降级到 `(claude_code, "")`，因为它喂的是
  system-prompt 构建路径，炸了会废掉整轮。降级值仍走同一 display 映射，宁可回退成
  一个"次真实"的默认，也不输出错误品牌。
- **未知 framework 名原样展示**（不塞进 `FRAMEWORK_DISPLAY_NAMES` 的名字直接回显），
  绝不替用户的私有框架名瞎编品牌。

## 上下游

- **被谁用**：[[basic_info_module.py]] `hook_data_gathering` 调它，填
  `ctx_data.agent_info_model_type`（framework 展示名）+ `ctx_data.model_name`
  （真实 model），再由 basic_info 的 [[prompts.py]] 模板 `{...}` 渲染进系统 prompt。
- **依赖谁**：只用 `db.get_one` 读 `agent_slots` / `agents` / `user_slots`。

## 契约测试

`tests/agent_framework/test_agent_model_identity.py`：覆盖胜出 / 无覆盖回退
user_slots / 缺 provider_id 不夺权 / **有 provider 但 framework NULL 不夺权**（PR #84
回归）/ 缺行→claude_code+空 model / DB 故障兜底 / 未知名原样。
`test_resolve_agent_framework_per_agent.py` 走委托后的 `_resolve_agent_framework_name`，
同样锁 dispatch 端行为——两个测试测的是同一份 overlay 的两个出口。
