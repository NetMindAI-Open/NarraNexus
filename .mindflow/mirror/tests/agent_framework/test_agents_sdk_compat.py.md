---
code_file: tests/agent_framework/test_agents_sdk_compat.py
last_verified: 2026-07-30
stub: false
---
# tests/agent_framework/test_agents_sdk_compat — openai-agents/openai 版本错配守门

openai 2.x 把 InputTokensDetails.cache_write_tokens 变必填,openai-agents 0.5 仍按旧形状构造→每次 Agents SDK 调用在 usage 解析处 ValidationError,helper 结构化输出 100% 白付一次失败再走 fallback(2026-07-30 dev,gpt-5.4-mini)。本测试直接构造 SDK 运行时会构造的对象(Usage() 默认、provider 只回 cached_tokens 的 PromptTokensDetails 归一),解析器选出不兼容组合时在 CI 就炸,不进 prod。
