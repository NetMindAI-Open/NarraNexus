---
code_file: src/xyz_agent_context/schema/__init__.py
last_verified: 2026-07-22
stub: false
---

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
