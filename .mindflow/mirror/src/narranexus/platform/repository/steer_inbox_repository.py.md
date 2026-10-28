---
code_file: src/narranexus/platform/repository/steer_inbox_repository.py
last_verified: 2026-08-24
stub: false
---

# steer_inbox_repository.py — 运行中插话注入的持久 owner

live-steering 的存储层:producer 往 `steer_inbox` append 一条注入(keyed by 不透明
`run_id`——RunRegistry 的运行句柄,本层不解释),transport 在下个 step 边界把某个 run 的
未消费行 drain 进它的 SteeringInlet 并 mark_consumed。**存在理由=解耦 + per-run 游标,不是
持久化 team 消息**(那本来就在 bus_messages):不仅仅是 bus,单聊插话不进 bus,统一 inbox 让
feeder 只 drain 一处(同 artifact_events outbox);`consumed_at` 是 bus (agent,channel)
游标给不了的 per-run 游标。详见 [[steer_schema.py]]。

## 四个操作

- `append` 幂等:**原子 INSERT-or-detect-UNIQUE**(同 channel_seen 的 mark_seen,**无前置 SELECT**)——
  插入,`is_unique_violation`(双方言共用判据)**只吞唯一约束违反**→返 False,其它错误(连接/schema)
  照样 raise。去掉前置查询后,幂等测试才真正覆盖到 except 分支(之前被 pre-check 短路,那条分支永远
  跑不到)。
  **写边界三道闸(铁律 #16:拒绝,绝不丢/截断)**:① VARCHAR 列(run_id/msg_id/sender_id/role)超
  `varchar_width` 就 raise ValueError——**只拒不夹断**(两条超长 id 夹成同一条会破坏去重;role 也无理由夹);
  SQLite 静默接受、MySQL 1406,不挡就本地全绿上云全炸。source 是 Literal 靠类型兜底、content 靠 ②。
  ② content 超 `MAX_CONTENT_BYTES` raise。③ 单 run 未消费
  ≥ `MAX_UNCONSUMED_PER_RUN` 抛 `SteerInboxFull`(back-pressure,让 producer 排队/回压)。bound 放在
  写边界是 steering.py 契约明写的(drain 侧整批取不设限)。
- `pull_unconsumed(run_id)`:该 run 的 `consumed_at IS NULL` 行,按 id 升序=到达序(FIFO);**不 LIMIT**
  (drain 不截断,铁律 #16)。
- `mark_consumed(run_id, up_to_id)`:给 id ≤ 游标且未消费的行盖章,返回条数;scoped 到 run_id 且只碰
  未消费——不动别的 run,重复消费 no-op。id 上限让 drain 只消费它看见的窗口、之后到达的留 pending。
  盖章走 `to_datetime6_literal`,与 created_at 的 `datetime('now')` 默认**同字节格式**(raw 参数不过 dict
  序列化,格式得手动统一,否则 SQLite 上两种格式不可比)。
- `cleanup_older_than_days(days)`:家族 retention 契约(同 channel_seen/lark_seen/channel_trigger_audit,
  后续 PR 挂到每日 cleanup tick)。两道保险:`consumed_at IS NOT NULL`(长跑 run 未注入的绝不删,铁律 #16);
  按 `created_at` 裁(格式统一),不按 consumed_at。best-effort,驱动错吞成 0。

## 投递语义(单 drainer)

`consumed_at` 给的是**单 drainer 前提下**的 at-most-once(一个 run 一个 loop 一个 transport,即设计)。
`pull` 与 `mark` 之间无 claim,两个 drainer 同 run 会各注入一遍(同 artifact_events 的 at-least-once 取舍)。
若将来一个 run 可能有两个 drainer(executor 被 reaper 回收后替身接管同 run),改 claim-then-read
(带条件 UPDATE 的 affected_rows 当 claim,见 job_repository.try_acquire_job),别只靠 consumed_at。

## 坑

不是 BaseRepository 子类:自增 id、无实体主键、全是 scoped range 查询(同 artifact_event 的理由)。
读仍返 `SteerInjection` 给 provenance 带类型。双方言 `%s` 参数化,标识符别加引号。`cleanup`/COUNT 的生
SQL 有 MySQL twin 覆盖。

## 2026-08-23(补)— mark_consumed_by_msg_ids:按 msg_id 精确消费

新增 `mark_consumed_by_msg_ids(run_id, msg_ids)`:按**确切的 (run_id, msg_id) 集合**盖 consumed_at(而非 `mark_consumed`
的行-id 天花板),因为消费端(loop)报的是它 drain 的 msg_id,不必把行 id 穿过 transport 回来(`append` 仍返 bool)。
一次 drain 取整个队列窗口,故「精确集合」与「天花板」等价、但更直接。`IN (%s,…)` 变长占位符=新 raw SQL→配了
MySQL twin(`test_steer_inbox_mysql`)。scoped `consumed_at IS NULL`(重复消费 no-op)、`to_datetime6_literal`(与
created_at 同字节格式)。**这是 steer_inbox 第一个真正的生产消费点**——补上了「bus 写进来的行永远 NULL、retention
永不生效」的 #2 洞。

## 2026-08-24(补)— discard_run:release 时回收从没 drain 的孤儿行

`discard_run(run_id)`:`DELETE FROM steer_inbox WHERE run_id=%s AND consumed_at IS NULL`。谁负责收孤儿行=**bus
在 run release 时**(见 [[message_bus_trigger.py]] `_discard_steer_orphans`)。用 DELETE 不用 mark_consumed:把从没
进模型的行标 consumed 会让「哪些插话真进了模型」的审计失真。必须在最后一次 drain 之后跑(否则与 deliver_consumed
竞态→真丢失)。补上 push-但-turn-先结束 那类行的 retention 洞(mark_consumed_by_msg_ids 只覆盖真被消费的)。

## 2026-08-24(补2)— discard_run 的调用顺序(修正)

`discard_run` 由 `_discard_steer_orphans` 在 **run release 之后**调(靠 AsyncExitStack LIFO:discard 注册在 release
注册之前 → 后退栈 → 后跑)。加上 `_route_steer` append 前的 `live_run is run` re-check,竞态窗口收窄(非关闭:get_db_client+append 仍 yield,残留行由 retention 的孤儿臂回收)。docstring 的
「Called when a run is released / after the last drain」现在与实现一致(见 [[message_bus_trigger.py]] 补8)。

## 2026-08-24(补3)— cleanup_older_than_days 增孤儿臂(结构性兜底)

`cleanup_older_than_days(days, orphan_days=7)` 现在两臂:①consumed 且过 `days`(常规 retention,`consumed_at IS NOT NULL`
护长跑 run 未注入行);②**still unconsumed 且过 `orphan_days`**——孤儿的结构性兜底(teardown discard 的竞态残留)。
`orphan_days=7` 刻意 generous:远超任何 turn(含铁律#14 的几十小时 job——其行随 run 边跑边 drain,不会未消费好几天),
所以**绝不删活跃 draining run 的合法待注入行**。这把 discard 竞态残留从「永久泄漏」降成「≤orphan_days 后被扫」。
注:`cleanup_older_than_days` 全仓仍无调用方(#351 遗留,daily tick 待后续 PR),兜底就位但要 tick 接上才真跑。

## 2026-08-24(补4)— cleanup 已接线(修正 补3)

补3 的「全仓仍无调用方」**已作废**:`MessageBusTrigger._maybe_run_steer_cleanup`(startup+每日)现在真调
`cleanup_older_than_days(STEER_RETENTION_DAYS, STEER_ORPHAN_DAYS)`(见 [[message_bus_trigger.py]] 补10)。retention 两臂
(consumed 过 days / unconsumed 过 orphan_days)在生产上会跑;`orphan_days` 由调用方常量给(repository 只 CRUD)。
新增 `idx_steer_inbox_created` 支撑 DELETE 的 created_at 过滤。
