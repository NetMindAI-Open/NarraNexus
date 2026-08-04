---
code_file: tests/backend/test_openai_compat_classify.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — _classify_event 空白回复丢弃用例

钉住 extract_reply_text 第五个消费方的口径：回复工具事件文本剥空
（全 citation / 纯空白）→ 整条丢弃（None），不得漏成 tool_call 把内
部 MCP 回复工具名暴露给外部 OpenAI-compat 客户端；真实回复照走
content；非回复工具照走 tool_call。lark_cli「同名工具非发送命令仍是
真 tool_call」的场景由 test_manyfold_im_ingress.py 既有用例钉住。
