---
code_file: src/narranexus/platform/narrative/_narrative_impl/anchor_rules.py
last_verified: 2026-08-27
stub: false
---

# anchor_rules.py — "锚点可不可以直接续"的唯一定义 + 会话间隔

## 2026-08-27(round 5)— advance_session_anchor 归队(I3)

会话锚点推进是一条锚点规则,兄弟们都在本模块而它留在 service 上。搬入
(纯函数,签名不变);select() 与 merged_select 直接调模块函数,service
不再持有它。

## 为什么存在

`is_reusable_anchor` 曾以两份字面量分别活在快慢路径里,两条路径为同一
不变量打架(2026-08-21 review Important 3);收敛为一份后一直住在
narrative_service。2026-08-27 合并路径编排搬进 `_narrative_impl/`
(见 [[merged_select]]),impl 不能上行 import service,于是定义下沉到
本文件;narrative_service **原样 re-export**,`step_1_fast_select` 的
公共 import 面不变。`minutes_since` 同迁(合并 prompt 的 elapsed 输入,
naive 时间戳按 UTC 读的守卫原样保留)。

## 坑

- 消费者四处:select() 连续性守卫、`_land_no_topic_turn`、
  step_1_fast_select 会话复用、合并路径锚点槽。改语义前把四处全看一遍。
- 读 `config.NARRATIVE_DEFAULT_BUCKETS_ENABLED`(函数内 lazy import,
  防 config↔impl 环)。
