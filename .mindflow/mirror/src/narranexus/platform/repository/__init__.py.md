---
code_file: src/narranexus/platform/repository/__init__.py
last_verified: 2026-09-09
stub: false
---
## 2026-09-09 — 导出 `BusDeliveryReceiptRepository`

bus 投递回执（`bus_delivery_receipts` 表）的读写方进公共导出面（import + `__all__`）。
纯转发改动。见 [[bus_delivery_receipt_repository]]。

## 2026-09-09 — 导出 `OwnerNoticeCooldownRepository`

owner 系统通知冷却窗（`owner_notice_cooldowns` 表）的读写方进公共导出面
（import + `__all__`）。纯转发改动。见 [[owner_notice_cooldown_repository]]。

## 2026-08-27 — 导出 `ChannelIngressBreakerRepository`

ingress 分级熔断的持久化面（`channel_ingress_breaker` 表）进公共导出面
（import + `__all__`）。纯转发改动，无行为变化。见
[[channel_ingress_breaker_repository]]。

**注意本次合入时它零调用点**——熔断器内核先合、接线后合（见
[[ingress_guard]] 开头那节）。所以「导出了但没人用」是刻意的，不是漏接。

## 2026-08-19 — 导出 `GatewayKeyMisuseRepository`

网关 key 异常/越权使用事件（`gateway_key_misuse` 表）的命中写方进公共导出面
（import + `__all__`）。纯转发改动，无行为变化。见 [[gateway_key_misuse_repository]]。

## 2026-08-13 — 导出 `BanAuditRepository`

账户状态变更审计（`ban_audit` 表）的数据访问进公共导出面（import + `__all__`）。
纯转发改动，无行为变化。见 [[ban_audit_repository]]。

## 2026-08-05 — 导出 `AgentRegistryRepository`

`bus_agent_registry`（同伴发现名录）的数据访问进公共导出面。该表原先由三处
内联代码各写各的（bus 模块每轮钩子、`bus_register_agent` 工具、
`InstanceFactory._register_agent_in_bus`），现在收敛成
[[agent_registry_repository]] + [[agent_discovery_sync]] 单点策略，调用方从包门面
import 即可。纯转发改动。

## 2026-07-29 — 移除 `CliSessionRepository` 导出

`cli_session_repository.py` 随 T7 删除:它 CRUD 的 `agent_cli_sessions` 表已摘掉
注册(见 [[schema_registry]]),而句柄机制整体被"每轮自建 transcript"取代
(见 [[transcript]])。纯转发改动。

# repository/__init__.py — repository 包的集中导出门面

## 2026-07-25 — 导出 CliSessionRepository

新增 re-export `CliSessionRepository`（可 resume 的 CLI 会话句柄数据访问,表
`agent_cli_sessions`）。纯导出改动,无行为变化。见
[`cli_session_repository.py`](cli_session_repository.py.md)。

## 为什么存在

集中 re-export 数据访问层的所有 Repository（Event / Narrative / SocialNetwork / Job /
Inbox / Agent / AgentMessage / MCP / User / Instance / Team / SkillArchive 等,都继承
`BaseRepository`），让别处统一 `from narranexus.platform.repository import XxxRepository`,
无需记住每个 repo 住在哪个子文件。也顺带 re-export 几个常用 schema 实体（`Agent` /
`User` / `MCPUrl` 等)方便调用方。新增 repo = 在这里加一行 import + 补进 `__all__`。

## 2026-07-13 — 导出 AgentCircuitBreakerRepository

新增 re-export `AgentCircuitBreakerRepository`（实时层 Agent 熔断器状态的数据访问,表
`instance_agent_circuit_breaker`）。纯导出改动,无行为变化。见
[`agent_circuit_breaker_repository.py`](agent_circuit_breaker_repository.py.md)。

## 2026-08-11 — 导出 `TeamBulletinRepository`

见 [[team_bulletin_repository]]。