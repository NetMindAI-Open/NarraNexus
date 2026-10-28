---
code_file: plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/_nexus_plugins_impl/state.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2f.1）— 提案 / 预算 / 审计时间线（都在插件 home）

`.proposals.json`（审批卡；`list` 时把超过 10 分钟的 pending 标 expired，`decide` 遇到过期直接拒绝——fail-closed；
决定只能做一次）、`.budgets.json`（每 Agent 的窗口计数/回滚计数/manual_required）、`.audit.jsonl`（append-only，
who/when/why/diff hash/report——「我的实例改了什么」时间线，工场 API 上报的 UI 错误也写这里供 observe 读）。
复用 lifecycle 的原子写与文件锁。`summary_for_agent` 给指令块用，永不抛。
