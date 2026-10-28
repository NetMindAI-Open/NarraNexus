---
code_file: tests/agent_framework/test_output_transfer_tool_result.py
last_verified: 2026-09-24
stub: false
---

# Claude SDK 工具结果的扁平化

钉住 `_stringify_tool_result_content`：列表内容取文本而不是 Python repr（前端对 tool_output 做
JSON.parse，artifact 发现依赖它）；非文本块走 json.dumps 保持可解析。2026-09-24 起图片块（SDK 与 MCP
两种形状）只保留描述、绝不内联 base64——这个字符串会进前端、数据库和后续历史回放。
