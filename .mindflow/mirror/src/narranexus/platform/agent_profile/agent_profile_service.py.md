---
code_file: src/narranexus/platform/agent_profile/agent_profile_service.py
last_verified: 2026-08-18
stub: false
---

# agent_profile_service.py — 协议层，纯转发

只导出 `apply_agent_profile_change` 与 `AgentProfileWrite`，实现在
`_agent_profile_impl/`（私有，永不外露）。调用方 import **包**
（`from narranexus.platform.agent_profile import ...`），不碰私有叶子。

只有一个函数，是因为改名是**一次事务**而不是一次列写入——为什么这个区分是承重的，
见 [[profile_write]]。
