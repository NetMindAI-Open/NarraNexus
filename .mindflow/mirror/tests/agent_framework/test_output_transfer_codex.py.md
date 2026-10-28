---
code_file: tests/agent_framework/test_output_transfer_codex.py
last_verified: 2026-09-24
stub: false
---

# Codex exec 事件翻译

每个用例喂一条真实形状的 `codex exec --json` 事件，断言翻译成 ResponseProcessor 消费的
OpenAI-Agents 形状：丢弃信息事件、`aggregated_output` 字段名、非零退出码、`mcp_tool_call` 用
server/tool 拼名、usage 与生命周期。2026-09-24 起 MCP 结果里的图片只以描述出现在输出里。
