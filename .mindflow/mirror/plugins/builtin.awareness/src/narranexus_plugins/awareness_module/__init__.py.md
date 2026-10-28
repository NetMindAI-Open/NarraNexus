---
code_file: plugins/builtin.awareness/src/narranexus_plugins/awareness_module/__init__.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-18 (四改) — 门面换了内容

`apply_agent_profile_change` / `AgentProfileWrite` **移出**（现在在
`narranexus.platform.agent_profile`，见 [[_overview]]）。新导出的是两个身份记录
写入器 `record_identity_change` / `reconcile_identity_record`——它们现在由包外
（那个领域包）调用，所以去掉了 `_` 前缀；跨边界不外露私有符号。
字符串助手新增 `build_identity_reconciliation_note` / `identity_note_asserts`。

## 2026-08-18 — 门面新增 `apply_agent_profile_change` / `AgentProfileWrite`

改名事务下沉后（见 [[_awareness_writes]] 同日条目），共享的是**结构化**入口而不再只是
那个返回字符串的渲染器：两个 HTTP 写入方（[[auth]] 的 `PUT /agents/{id}`、
manyfold 的 `PATCH`）欠客户端状态码，得拿到 `error_kind`。二者与既有符号一样从
`_awareness_writes` 纯转发，调用方 import **包**而不是私有叶子。

# awareness_module/__init__.py — package surface

从 [[_awareness_writes]] re-export 共享写助手 `update_agent_profile_from_args` 及其身份笔记
字符串助手/常量（build/merge/IDENTITY_CHANGE_SECTION/MAX_IDENTITY_CHANGE_ENTRIES），供 seam 的
[[store]] DirectStore、backend 孪生路由 [[profile]]、以及测试 import **包**而非私有叶子（同
job_module 先例）。**故意不** re-export AwarenessModule 类：module_registry 已从 .awareness_module 引它，
再在此 re-export 会给「只想拿 write helper 的人」加一层对该子模块的初始化顺序依赖（同
job_module 先例）。**不是**为省模块加载成本——import `narranexus.platform.module_system` 任一子模块
都会先跑父包 module_registry。纯转发。

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.
