---
code_file: src/narranexus/platform/utils/db/dialect_errors.py
stub: false
last_verified: 2026-08-19
---

# dialect_errors.py — DB 错误跨方言分类

## 为什么存在

「唯一键冲突」的判定曾被手抄进六个 insert 落点：两个 seen-message 去重仓
（channel / lark）、product-analytics 写方、instance-link 竞态守卫、
gateway-key-misuse 幂等守卫、bundle importer。六份已经漂移——有的漏了 MySQL
`1062` 错误码，bundle importer 那份则宽到只匹配裸子串 `unique` / `duplicate`
（会把无关错误误判成冲突）。唯一冲突长什么样与「哪张表抛的」无关，属 **DB 层**
性质，故收口到这里（挨着另一个跨方言 helper [[dialect_time.py]]），而不是塞进
某个 repository——否则 bundle importer 得跨域 import 一个 repository 才能拿到
谓词（铁律 #8）。

## 提供什么

- `is_unique_violation(exc)` —— SQLite/MySQL 双方言唯一键冲突判定，不 import 任何
  驱动（按错误文本匹配）：aiosqlite 抛 `"UNIQUE constraint failed: ..."`，aiomysql
  抛 `"(1062, \"Duplicate entry ...\")"`。刻意收窄成完整短语（而非裸 `unique` /
  `duplicate`），避免把文本里恰好含这些词的无关错误误判成冲突。

## 消费方（六处，语义各异，谓词只换判定不动分支）

- [[gateway_key_misuse_repository]]：命中 `(key_hash, hit_at)` 唯一索引 → 幂等回同一行。
- [[channel_seen_message_repository]] / [[lark_seen_message_repository]]：冲突 → `return
  False`（已见过、丢消息）；**非**冲突向上抛，让调用方 fail-open（lark 的 `return False`
  是 fail-closed，谓词化后语义不变）。
- [[product_analytics_repository]]：冲突 → 静默 `return`（首条事实胜，重放不覆盖维度）。
- [[instance_link_repository]]：冲突 → `return 0`（竞态下链接已存在）。**顺手补 1062**
  （原缺，是 bug）——该表仅一个复合唯一键 `uk_instance_narrative(instance_id,
  narrative_id)`，任何唯一冲突即该竞态。
- `bundle/importer.py`：冲突 → 记 `nl_dups`（跨 agent/文件重复）。**顺手收紧**（原匹配裸
  `unique`/`duplicate`）——bundle import 测试确认真实冲突文本含 "unique constraint
  failed"，收紧不影响既有 dedup。
