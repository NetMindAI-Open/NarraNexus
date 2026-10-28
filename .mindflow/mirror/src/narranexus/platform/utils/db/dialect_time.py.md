---
code_file: src/narranexus/platform/utils/db/dialect_time.py
stub: false
last_verified: 2026-08-10
---

# dialect_time.py — DATETIME 单元格跨方言归一

## 为什么存在

sqlite 驱动对 DATETIME 列返回 `datetime` 对象,mysql 返回字符串——
任何要排序/比较时间单元格的代码都得先归一。这是 **DB 层**的性质而非
某张表的私事:它曾以私有名 `_event_time_str` 被拷贝进两个审计仓库,
且被 backend 路由跨包 import——公有化 + 收口到 utils/db 是 review
两轮点名后的归宿(铁律 #8)。

## 消费方

channel_trigger_audit_repository、lark_trigger_audit_repository、
backend/routes/manyfold/diagnostics——全部直接使用公名,无别名。
