---
code_file: src/narranexus/platform/agent_profile/__init__.py
last_verified: 2026-09-04
stub: false
---

# agent_profile/__init__.py — 包门面

只转发 `apply_agent_profile_change` 与 `AgentProfileWrite`（来自
[[agent_profile_service]]）。调用方——两个 HTTP 路由、Awareness 的 MCP 渲染器——
import **包**，不碰 `_agent_profile_impl/`。

docstring 里那段「为什么是领域包而不是留在 Module 里」是**摘要**，完整论证（含
铁律 #3 的代价、延迟导入到底防住了什么）在 [[_overview]]。这里不重复，两份 intent
下次只会更新一份。

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.
