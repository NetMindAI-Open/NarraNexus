---
code_file: src/xyz_agent_context/agent_framework/openai_agents_sdk.py
last_verified: 2026-04-10
stub: false
---
# openai_agents_sdk.py — Helper LLM 适配层（结构化输出 + 兼容 think-block 模型）

## 为什么存在

Narrative 选择、Module 决策、数据提取等辅助 LLM 调用需要结构化输出（Pydantic model），而系统支持多种 OpenAI-compatible 端点（官方 OpenAI、NetMind、Yunwu、本地模型）。问题是不同模型对 `response_format` 的支持差异很大：minimax、deepseek 等会返回 `<think>...</think>` 推理块，无法直接解析为 JSON。这个文件提供统一的 `llm_function()` 接口，优先走 OpenAI Agents SDK 结构化输出路径，失败后自动降级到手动 JSON 解析路径，并通过 blocklist 机制避免对已知不支持结构化输出的模型重试。

## 上下游关系

被 `narrative/` 包（Narrative 选择决策）、`module/_module_impl/`（Instance 决策）等需要 helper LLM 的地方调用。调用者传入 `instructions`、`user_input`、`output_type` (Pydantic class)，拿回 result 对象后读 `result.final_output`。

配置读自 `api_config.openai_config`（ContextVar proxy），确保多租户并发安全。`model_catalog.get_max_output_tokens()` 提供每个模型的 token 上限。

和 `xyz_claude_agent_sdk.py` 的区别：这个文件处理有限上下文的"工具性调用"（决策、提取、分析），Claude SDK 处理无限 turn 的完整 agent loop。两者不互相调用。

## 设计决策

**运行时 blocklist**：`_structured_output_blocklist` 是进程级 set，第一次遇到结构化输出失败的模型就加入，后续所有调用直接跳 SDK 走 fallback。这样不需要配置文件，自动适应新模型。缺点是 blocklist 不持久化，进程重启后会重新尝试一次 SDK 路径。

**`_resolve_model()` 的三种模式**：`"default"` sentinel 值允许调用方指定 per-call 的模型名（官方 OpenAI 多 model 场景）；指定具体 model 且官方端点时强制用该 model；非官方端点时总用 slot 配置的 model（代理端点往往只支持特定模型名）。

**`max_completion_tokens` vs `max_tokens` fallback**：先试 `max_completion_tokens`（新 API），如果 provider 报错再 fallback 到 `max_tokens`（旧 API）。这是为了兼容不同 provider 的 API 版本差异。

**`_extract_json_from_llm_output()` 的穿透逻辑**：先剥 `<think>` 块，再剥 markdown code fence，再用正则找最外层 JSON object/array。能处理大多数"乱七八糟"的 LLM 输出，但对嵌套结构不规范的输出可能误提取。

## Gotcha / 边界情况

- blocklist 是进程级全局变量，一个用户触发的模型失败会让所有用户的该模型都走 fallback 路径。这在单模型多用户场景下是期望行为，但如果不同 provider 用同一个 model name 则可能误 block。
- `_SimpleResult` 和 `_ParsedResult` 是私有包装类，调用方不应该直接 isinstance 检查它们。

## 新人易踩的坑

- `result.final_output` 在没有 `output_type` 时是字符串；有 `output_type` 时是 Pydantic model 实例。两种情况的类型完全不同，调用方需要根据是否传了 `output_type` 来决定如何处理返回值。
- 测试时如果用假的 `openai_config.base_url`（非官方端点），`_resolve_model` 会强制用 slot 配置的 model name，即使你传了其他 model 名也不生效。
