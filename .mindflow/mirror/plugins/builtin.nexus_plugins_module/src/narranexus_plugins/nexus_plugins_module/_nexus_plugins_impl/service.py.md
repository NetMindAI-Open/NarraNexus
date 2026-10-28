---
code_file: plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/_nexus_plugins_impl/service.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2f.1）— `SelfExtensionService`：plugin_* 动词的本体

docs → scaffold（`<workspace>/plugins/<id>`，用 CLI 同一 `scaffold`）→ edit（护栏 + `.plugin-changelog.jsonl`）
→ validate → test（报告落 `.test-report.json`）→ register（要当前树的绿报告；已注册则版本必须递增；link 安装、
`scope=agent:<id>`、`installed_by=agent:<id>`、注册后 **disabled**）→ activate（生成提案，用户在工场页决定；
hooks/routes/workers/tools/mcp 类必须先 agent 范围且 observe 过才能 global）→ `apply_decision`（工场 API 调：
批准则 enable + scope + 权限已确认）→ observe（状态/crash/UI 错误/审计条数）→ deactivate/rollback（两次转人工）
/diff/install/upgrade（都是提案）/publish_hint。每步进审计。云端构造即拒绝。不 import `backend`（铁律 #21）。
