---
code_file: src/narranexus/platform/repository/artifact_event_repository.py
last_verified: 2026-08-20
stub: false
---

# artifact_event_repository.py — outbox 表的唯一 owner(#334 I9)

此前 `instance_artifact_events` 无主:notify 直插、BackgroundRun 揣着
两条手写 SQL,为够到表还在 BaseRepository 上开了 `db` property 逃逸口
——三个文件持一张表,仓储层边界降级成建议。现在读写全在这里
(stage / pending_for_agent / mark_consumed),逃逸口已删,改表结构
只动一处。

## 投递语义:至少一次(显式声明)

同一 agent 的并发 run 可能在各自 mark_consumed 前读到同一批行 →
同一事件发两遍。**这是接受的**:前端 applyEvent 的 updated_at 单调门
吃掉重复 upsert,repointed toast 按 artifact_id 去重;event_stream 里
的双记录是审计噪音不是错误。若未来出现不能幂等的消费者,再改
「先 UPDATE 抢占再读」,别默默假设恰好一次。

## 坑

mark_consumed 的 IN-list 是变长 placeholder,双方言都走 %s 参数化;
标识符别加引号(MySQL 1064)。
