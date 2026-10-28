---
code_file: packages/narranexus-contracts/src/narranexus/contracts/llm_client.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 1）— `resolve_helper_model`：三份 `_resolve_model` 合一的那条规则

规则只有一条：slot 非空且不是 `"default"` 哨兵就用 slot；否则「与调用点同一模型命名空间」的
客户端（`honour_requested=True`，openai 协议）用调用点偏好，其余（anthropic / cli）用各自默认。
研究文档 H9 说三份实现「各写各的」，合一时发现 openai 版的「官方端点 vs 自定义端点」两种模式返回值
相同，端点判断本来就不影响结果（`test_openai_helper_official_and_custom_endpoints_agree` 钉住）。
纯函数、stdlib-only，第三方 `llmClients` 插件直接复用。`ModelResolver` Protocol 是 `model.resolver` 位的
契约符号（可替换的解析策略），`resolve_helper_model` 是它的内置实现规则。
openai 客户端在 slot 为空时原本回落到自身默认，现在与 `"default"` 哨兵同样先看调用点偏好——
`test_openai_helper_empty_slot_honours_call_site` 钉住这一行为。

## 2026-09-03 — helper LLM 客户端契约（用户点名的「LLM client」轴）

三个现有 SDK（`anthropic_helper.AnthropicHelperSDK` / `adapters.openai_agents.OpenAIAgentsSDK` /
`cli_helper.CliHelperSDK`）共享 `llm_function` / `llm_stream` 同构接口，`helper_sdk.py` 用协议键
分派。本文件把那个隐式同构写成 `LlmClient` Protocol，签名以三者实际参数为准
（`instructions, user_input, output_type, model, agent_id, db, reasoning_effort`）。
`model` 明文标注为「调用方偏好，客户端可忽略」——这是三份 `_resolve_model` 的共同语义，
批 1 合一时挂到这里。
