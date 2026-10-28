---
code_file: tests/module/test_browser_vision.py
last_verified: 2026-09-24
stub: false
---

# browser_look 的 MCP 形状

经真实的 instrumented MCP server 调用：成功时是 `[text, image]` 两部件的原生 `CallToolResult`，
元数据 JSON 不含 base64，且经过 FastMCP 的 convert_result 后图片仍保留；失败只有文本并置 isError；
缺少运行时身份时不触碰浏览器会话。
