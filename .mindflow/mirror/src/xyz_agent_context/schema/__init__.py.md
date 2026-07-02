---
code_file: src/xyz_agent_context/schema/__init__.py
last_verified: 2026-06-17
stub: false
---
# schema/__init__.py — schema 包的集中导出门面

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
