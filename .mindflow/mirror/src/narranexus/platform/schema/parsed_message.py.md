---
code_file: src/narranexus/platform/schema/parsed_message.py
last_verified: 2026-08-21
stub: false
---

## 2026-08-21 — `UNKNOWN_SENDER_NAME` 常量（PR-2 增量审 Minor）

`ParsedMessage.sender_name` 的占位默认值 `"Unknown"` 提成模块级常量 `UNKNOWN_SENDER_NAME`。动机:reach 记录([[inbox_recorder.py]])在决定「首触实体要不要带名」时会拿 `counterpart_name != "Unknown"` 判断——若哪天改 schema 默认值(如换成 `""`),硬编码的 `"Unknown"` 比较会安静失配、把占位名当真名写进社交实体的 `entity_name`(而那正是 §3b 拿来搜人的字段)。三处引用(schema 默认值 + `inbox_recorder` 的 `display` 计算 + `entity_name_if_new` 判断)统一指向常量。

`ChatType`(PRIVATE / GROUP / TOPIC_GROUP)是 reach 记录「只记 1:1」判据的类型来源:各渠道 `parse_event` 正向白名单出这个值,`inbox_recorder._record_reach` 只在 `== PRIVATE` 时记。枚举本身本次未变。
