---
code_file: src/xyz_agent_context/repository/service_audit_repository.py
last_verified: 2026-05-29
stub: false
---

# service_audit_repository.py — service_audit 表的数据访问层（ServiceAuditRepository）

## 为什么存在

通用的 `service_audit` 表（每个后台循环共享的 append-only L2 黑匣子）的访问层。
从 channel 专属的 `LarkTriggerAuditRepository` 泛化而来，让任何服务按 `service` 名
共用一张表，而不是每个 poller 各建一张审计表。

## 接口

- `record(service, event_type, detail)` — best-effort 追加，**绝不抛异常**（审计是
  辅助性的，写失败不能拖垮被它观察的循环）。`detail` JSON 序列化，加字段不用迁移。
- `recent(service?, event_type?, limit)` — 倒序切片，可过滤。
- `last_heartbeat(service)` — L2 健康检查回答"这循环还活着吗"所需的唯一查询。

## 坑

- DB client 以无类型方式注入（沿用 LarkTriggerAuditRepository 的写法）——这里 import
  具体 client 类只会增加加载顺序耦合，没有收益。
- 事件词汇（started/stopped/heartbeat/error）在本文件定义为模块常量，被
  `services/service_audit.py` 复用，两边保持同步。
- 表定义 + 索引在 `utils/schema_registry.py`（`service_audit`），由 auto_migrate 进程
  启动时幂等创建——禁止手写 DDL。
