---
code_file: src/narranexus/platform/schema/__init__.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 导出 `OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE`

门面 re-export（[[runtime_message.py]]），供 `circuit_breaker` 从 `schema` 顶层引用。纯转发。

## 2026-08-27 — 导出 `ChannelIngressBreaker` / `session_key`

ingress 分级熔断的行模型与会话键构造函数进公共导出面。`session_key()` 单独
导出是因为**它是键格式的唯一定义处**：键宽由 DDL 推导并有测试钉着，四个
组成部分（agent_id / channel / chat_id / sender_id）少任何一个都会让重复率
退化成与内容无关的常数。见 [[channel_ingress_breaker_schema]]。

本次合入时零调用点（内核先合、接线后合）。

## 2026-08-26 — 导出 Step-3 相位常量（`PHASE_BUILD_CONTEXT_*` / `PHASE_RUN_AGENT_*`）

把 [[runtime_message]] 新增的 4 个相位常量放上公共导出面。消费方:
[[step_3_agent_loop]](发相位)、[[run_recorder]](派生 `current_stage`)、
`tests/agent_framework/test_step_3_framework_dispatch` 与
`tests/agent_runtime/test_run_recorder`(对着常量断言)。纯转发。

## 2026-08-17 — 导出 `normalize_agent_text` / `agent_field_matches`

[[entity_schema]] 新增的「这次写会不会改变什么」判断进公共导出面——`agents` 行的
两个写入方([[auth.py]] 的 `PUT /api/auth/agents`、[[_awareness_writes]] 的
`update_agent_profile`)都从 `xyz_agent_context.schema` 引,等价规则只有一份。
它们此前各写一份且**答案相反**(一个比较 strip 过的值、一个比较原样值),所以
这次上提不是整理,是修 bug。纯转发。

注:`backend/` 引 `xyz_agent_context` 是合法方向,反向不行——所以共享的那份
必须落在 package 里,这也是它没被放进 backend 的原因。

同日追加:`AGENT_TEXT_MAX_LENGTH`、`normalize_agent_row_text`、
`AGENT_TEXT_FIELDS` 进门面。

> 更正:第一次给 `AGENT_TEXT_MAX_LENGTH` 写的理由是「api_schema 与写边校验都需要它,
> 放进门面后不必再深引」——**当时它零消费者**,两个消费者都还在深引。而且
> [[api_schema]] **结构上不可能**用门面:门面反过来再导出 api_schema 的模型,引它成环。
> 现在 [[_awareness_writes]](在 schema/ 之外,可以用门面)已改为门面引用。

第四轮再加 `StrippedText`。同时更正上面那句的后半:原写「api_schema 与 manyfold
路由保留深引,成环」——**成环只是 api_schema 的原因**(它在包内,门面反过来导出
它的模型);manyfold 在 `backend/` 下,引门面从来不会成环,它当时深引只是因为
`StrippedText` 不在门面里。现在 manyfold 已全部改走门面,只有 [[api_schema]]
保留深引,理由已就地注明。

## 2026-08-10 — 导出 `JobUpdateFields`

[[job_schema]] 新增的 job_update 可变字段集合进公共导出面——两个 backend 路由
body（前端 `JobUpdateBody` 加 agent_id、seam `JobUpdateSeamBody` 加
`extra="forbid"`）都从 `xyz_agent_context.schema` 顶层引，字段清单只声明一份。纯转发。

## 2026-08-04 — 导出 `is_agent_description_unset` / `LEGACY_AGENT_DESCRIPTION_PLACEHOLDER`

[[entity_schema]] 新增的"描述算不算没设置"判断进公共导出面——三个消费面
（[[message_bus_module]] 渲染、[[basic_info_module]] 自述、[[agent_discovery_sync]]
名录写入）都从 `xyz_agent_context.schema` 引，判据只有一份。纯转发。

## 2026-08-03 — 导出 `BUS_ERRAND_TURN_SOURCE`

[[hook_schema]] 新增的 bus 差事延续 turn-source 章常量进公共导出面——
trigger、context_runtime、测试都从 `xyz_agent_context.schema` 引。纯转发。

## 2026-07-29 — 移除 `AgentCliSession` 导出

`cli_session.py` 随 T7 删除(表已摘掉注册,见 [[schema_registry]]),门面同步去掉
re-export 与 `__all__` 条目。纯转发改动。

## 2026-07-29 — 导出 `AgentPlan` / `AgentReplyDelta`

跟着 [[runtime_message]] 新增的两个 NexusPower 专属型别一起进公共导出面——
消费方（[[response_processor]]、路由层）从 `schema` 顶层取，不去 reach 进
子模块。

## 2026-07-25 — 导出 `AgentCliSession`

门面新增 re-export `AgentCliSession`（[[cli_session]]，可 resume 的 CLI 会话
句柄模型），供 step_4 / repository 从 `schema` 顶层引用。纯转发。

## 2026-07-22 — export URL-tab models

Re-export `URL_ARTIFACT_KIND`, `EmbedMode`, `EmbedVerdict`, `UrlArtifactDoc`
from [[artifact_schema.py]]. Pure forwarding.
# schema/__init__.py — schema 包的集中导出门面

## 2026-07-21 — 导出 `HealCandidate` / `HealResult`

artifact heal 的结果模型从路由本地类提升进 [[artifact_schema.py]]，门面同步
re-export。纯转发。

## 2026-07-22 — 导出 `EXECUTOR_INFRA_ERROR_TYPE`

门面新增 re-export `EXECUTOR_INFRA_ERROR_TYPE`（[[runtime_message.py]]），供
`step_3_agent_loop` / `agent_circuit_breaker` 从 `schema` 顶层引用。纯转发。

## 2026-07-15 — 导出 `SELF_SERVICEABLE_ERROR_TYPE`

门面新增 re-export `SELF_SERVICEABLE_ERROR_TYPE`（与 `AUTH_EXPIRED_ERROR_TYPE`
并列，来自 [[runtime_message.py]]）。纯转发，供 `response_processor` /
`step_3_agent_loop` 从 `schema` 顶层引用而不触碰 leaf 模块路径。

## 2026-07-13 — 导出 Agent 熔断器 schema

新增 re-export：`CbStatus` / `PausedReason` / `ErrorCategory` / `PAUSING_CATEGORIES` /
`AgentCircuitBreaker`（来自 `agent_circuit_breaker_schema.py`），并补进 `__all__`。纯导出
改动,无 schema 形状变化。见 [`agent_circuit_breaker_schema.py`](agent_circuit_breaker_schema.py.md)。

## 为什么存在

集中 re-export 全仓所有 Pydantic 数据模型（Module / Instance / Context /
RuntimeMessage / Job / Inbox / Hook / Attachment / Decision / Entity / API / Skill /
A2A / Artifact 等），让别处统一 `from xyz_agent_context.schema import X`，无需记住每个
模型住在哪个子文件。新增模型 = 在对应 `from .xxx import (...)` 块里加一行，并补进
`__all__`。这是 schema 层的"单一入口"约定。

## 2026-06-17 — 导出 AUTH_EXPIRED_ERROR_TYPE，打破鉴权常量的循环 import

PR #25 把 `AUTH_EXPIRED_ERROR_TYPE = "auth_expired"` 这个常量下沉到 schema 叶子模块
（定义在 `runtime_message.py`，这里 re-export）。

背景（incident 2026-06-11）：该常量是鉴权 / 凭证失败（codex OAuth token 过期、
"refresh token already used"、401 等）的 `error_type` 标记，`response_processor` 和
`step_3_agent_loop` 都要用它——前者发 fatal `ErrorMessage` 时填，后者靠它跳过 no_reply
fallback。但若把常量留在 `response_processor` 里定义，就会闭合一条循环：
`response_processor` → `step_display` → `_agent_runtime_steps` → `step_3_agent_loop`
→ `response_processor`，import 期常量还没绑定。

解法是经典的"共享常量下沉到双方都依赖的叶子层"：`runtime_message.py` 是 schema 叶子，
不 import 上述任何运行时模块，两边都能干净 import 而不成环。本文件只是把它纳入 schema
门面的统一导出（加进 `from .runtime_message import (...)` 块）。注意 `__all__` 里这条
常量目前未列出（与 `ErrorMessage` 同样未列），但已可经包级 import 取用——纯导出改动，
无 schema 形状变化。

## 2026-07-30 — Agent Migration 导出

新增 `from .migration_schema import (...)` 块,导出 `StandardizedAgentImport`、
`MigrationSource/Agent/Skill/Memory/McpServer/Custom/Turn/Session`、`FrameworkDetection`、
`MIGRATION_SCHEMA_VERSION`、`AWARENESS_IMPORT_CHAR_LIMIT`,并列进 `__all__`。纯导出,
无 schema 形状变化。见 [[migration_schema]]。

## 2026-08-13 — 新增 `NON_TRANSACTING_USER_STATUSES` 再导出

从 [[entity_schema.py]] 再导出 `NON_TRANSACTING_USER_STATUSES`（`{banned, blocked, deleted}` frozenset，账户停用闸门的单一真相源），供 backend 三处停用面（auth middleware / 登录闸门 / suspend 路由）与未来 agent 包侧共用同一常量，避免规则在多处漂移。

## 2026-08-18 — owner 工具改名跟随

`send_message_to_user_directly` 拆成 `reply_owner`（回答刚说话的 owner）与 `notify_owner`
（未被问就主动告知）。两者行为相同但纪律相反，合成一个工具就要求模型每轮自己判断该用哪种
register。本文件里改到的是该 handler 注册的 `user_reply_tool_names` / 相关文案 —— 一两行，
但 registry 条目是**活的行为**：它决定哪些工具调用算作这个来源的一次回复，也是
`render_origin_declaration` 取 label 的同一条记录。规范解释见
[[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。

## 2026-09-03 — 再导出 `BoundChannel`

从 [[api_schema.py]] 再导出 `BoundChannel`（`AgentInfo.bound_channels` 的元素模型），
消费方是 `backend/routes/auth.py` 的目录投影。纯转发，字段语义见 api_schema 的 mirror。

Merged with the plugin platform (2026-09-06): pages, drawer panels, sidebar items, commands and agent-row badges come from the frontend registries (`platform/registries`, registered in `platform/builtin.ts`); this file keeps dev's behaviour on top of that.
