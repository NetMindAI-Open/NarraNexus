---
code_file: src/narranexus/platform/repository/channel_trigger_audit_repository.py
stub: false
last_verified: 2026-08-10
---

# channel_trigger_audit_repository.py — 通用渠道审计仓库

## 2026-08-10 — append 镜像一条 AUDIT 级日志行

sink 运的是日志记录不是 DB 行——不镜像的话 denied/silent/attachments/
processed 只能靠 pull 才可见。自定义 AUDIT 级(25)仅在 setup_logging
后存在,裸库使用(测试/脚本)回落 INFO 而不是丢行。

<!-- merged from channel_trigger_audit_repository.md (duplicate mirror, review #272-r3) -->

## 2026-08-10 — recent_for_agent + SQL 下推 + 归一器迁出

`recent_for_agent`(static:实例按 channel 构造,而 agent 轨迹跨
channel——诊断端点的查询形状);`recent()` 从全表拉取+Python 排序改
SQL order/limit;`count_by_type` 加列投影(不再拖整窗 details)。
`_event_time_str` 迁至 utils/db/dialect_time 公有,本模块调用点
**直接改用公名,不留别名**(铁律 #2:兼容 shim 在零外部消费者时
纯属摩擦)——曾在本仓库与 Lark 仓库各一份拷贝、且被路由跨包
import 私名。Lark 仓库的 twin(recent 全表拉取+排序、count_by_type
无投影)同批收口(铁律 #8:打开了文件就扫完相邻代码)。

## Why it exists

Generic multi-channel version of ``LarkTriggerAuditRepository``. The
trigger runs in its own EC2 container, where pulling logs out post-
incident is hard. This repository is the trigger's black-box recorder
— every interesting lifecycle event lands in one row.

Phase 1 ships this alongside the existing Lark-specific repo (no
behavioural change to Lark). Phase 2 will redirect Lark writes here
and drop the old repo.

## Design decisions

- **Best-effort writes that NEVER raise.** ``append`` swallows every
  exception and logs to loguru. Losing an audit row is always
  preferable to stalling real user traffic.
- **JSON ``details`` column.** Adding new fields to an event type
  doesn't require a migration — just stash into ``details`` and
  the new field flows into the JSON blob.
- **Per-channel cleanup + filtering.** Every query / cleanup adds
  ``channel = self._channel`` so one channel's bursty volume doesn't
  swamp another's queries.
- **String constants, re-exported.** Event types live in
  ``narranexus.platform.channel.channel_audit_events``. We re-export
  the common ones from this module so callers don't need a second
  import — same UX as the Lark version.

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase._audit`` (the only writer).
- **Downstream**: ``channel_trigger_audit`` table; consumed by future
  /healthz endpoints and admin UIs.
