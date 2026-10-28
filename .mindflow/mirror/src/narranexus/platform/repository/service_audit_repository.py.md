---
code_file: src/narranexus/platform/repository/service_audit_repository.py
last_verified: 2026-08-26
stub: false
---

## 2026-08-26 — `record()` 报告写入结果

原来返回 `None`，且**自己 catch 了 insert 异常**。对心跳、生命周期事件这类
调用方无所谓（写不进去就算了），但 [[service_audit]] 的 `_emit` 要把「行落
没落库」透传给调用方，而它拿到的「没抛异常」在这里恒真——插入炸了也一样。
于是 [[step_3_agent_loop.py]] 的 DM 兜底审计冷却仍然会在 DB 故障时被 arm，
正是加返回值要修的那件事，只是缺口移到了下一层。

现在返回 `bool`：落库 True，被 catch 掉的失败 False。**仍然不抛**——审计是
旁路，不能打断被观察的业务。其余调用方（`temporal_guard`、
`openai_agents`）忽略返回值，签名不变。

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
- 表定义 + 索引在 `utils/db/schema_registry.py`（`service_audit`），由 auto_migrate 进程
  启动时幂等创建——禁止手写 DDL。
