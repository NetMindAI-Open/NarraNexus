---
code_file: tests/message_bus/test_dunhuang_broken_chain.py
last_verified: 2026-08-14
stub: false
---

## 2026-08-14 — 为什么存在

钉住 2026-06-30 敦煌断链这个**创始事故的整条链**，而不是其中某一环。

事故形状：A3 收到交接后回了「收到，开始处理……完成后交付 @A4」，run 以
`completed` 收尾、`final_output` 全文就是那句承诺，六级流水线死在这里，
**没有报错、没有超时、没有任何可见状态**。

单元层面这些环节各自已有测试（[[test_patrol_stall_detection]] 判定、
[[test_patrol_candidates]] 候选、[[test_patrol_turn]] 落墙），本文件补的是
**它们必须composed起来**这件事：事故本身就是链式失效，任何单独一环能工作都
不足以阻止它。所以这里按事故走过的顺序串一遍——工作项活过 run → 平台从
`bus_agent_activity` 判定 stalled（铁律 #15，不压模型服从）→ 有 stalled 切
快节奏 → 无人 @ 的 team 也能被扫出来 → 平台身份落墙且不吃 hop 配额。

## 两条 residual 是**特性刻画**，不是期望行为

`test_a_promise_counts_as_a_delivery_so_the_notice_path_is_blind_to_it`：
#296 的 `system_undelivered` 覆盖的是**零投递**。A3 确实发了文本，
`reached_nobody` 为 False，所以那条链路结构上看不见敦煌形状——工作板是目前
唯一能兜住它的机制。这条断言是防止有人误以为 #296 已经关掉了这个 case。

`test_lineage_answers_which_tree_but_not_which_parent`：`events.root_run_id`
是 #252 级联停止要的**扁平继承标签**，故意不做 parent 指针（见
[[schema_registry]] 该列注释）。所以 PRD《异步协作》验收第 6 条里「父 run 可
查」今天没有列可读。钉在 schema 上，是因为找这个 gap 的人会去那儿找。

这两条**变红时应当更新本文件而非删除**——那说明产品语义真的动了，需要连带回
写 PRD 第四章。

## 关联

上游机制：[[patrol]]、[[team_work_repository]]、[[message_bus_trigger]]。
需求出处：飞书 PRD《异步协作：让「我干完再回来找你」成为可兑现的承诺》
（2026-08-06 立项，基线 e07c9497）。
