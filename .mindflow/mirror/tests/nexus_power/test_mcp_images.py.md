---
code_file: tests/nexus_power/test_mcp_images.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

钉住 `projected_chars`：无图消息与 `len(str(m))` 完全一致，图片按估算计而不按 base64 长度。

# MCP 图片穿过 Nexus Power 全链路

channel → dispatcher → ledger → projector：两个并行调用各带一张图，投影后 tool 消息连续且只有文本，
图片集中在随后一条 user 消息；token 估算不按 base64 长度爆表。事件日志只含图片描述（mime、长度、
sha256），provider 视图保留完整数据 URL。错误结果里的图片不会被字符串化成 base64。
