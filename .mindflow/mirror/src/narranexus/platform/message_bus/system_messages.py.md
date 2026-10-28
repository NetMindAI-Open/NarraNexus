---
code_file: src/narranexus/platform/message_bus/system_messages.py
last_verified: 2026-09-09
stub: false
---

# system_messages — 平台自述行的唯一真源

## 2026-09-09 — `_TRIGGER_LABELS` 增加 `DELIVERY_FAILED_MSG_TYPE`

第三种能成为触发源的平台行：收件方在 poison 阈值上被丢弃的消息，trigger 会 @ 发件方
（[[delivery_notice]] `announce_processing_failure`）。没有这条标签它会落到
`_TRIGGER_FALLBACK` 的「a platform notice」——发件 agent 醒来只知道「有个通知」，不知道
自己的消息已经死了。措辞点明「could not process your message and it was dropped」。

## 为什么存在

团队房间里有两类消息：agent 和人在说话，以及**平台在讲自己刚做了什么**（某个 run 被停了、
公告栏变了、巡查扫了一遍板子）。几乎每个要统计、采样、总结房间活动的消费者都得排除第二类，
而它们此前各自重打字符串字面量。

于是出了教科书式的两次事故，**方向还相反**：

- 总结 worker 里写了 `system_stop` 和 `system_bulletin`；`patrol` 来自另一个功能、晚一步落地，
  没有任何东西告诉 worker——一个专门用来阻止「平台自己触发自己」的过滤器，**从它不知道的那一侧被重新打开**。
- `_team_cascade_depth` 恰好相反：排除了 `patrol`，没排除另外两个。

两边各漏各的，而两边都以为自己写全了。

## 设计

元组放这里，消费者 import。加第四种类型是一次编辑，**而不是"记得去改三处 SQL 字符串并祈祷三处都找到了"**。

`placeholders()` 按元组长度生成占位符：此前三处查询把 `(%s, %s)` 的个数硬编码进 SQL 字面量，
那是又一处需要同步、又一处会漏。

**定义仍留在各自的功能里**（`patrol.py` / [[team_bulletin]]），本模块只做汇总——
这样新增一种类型不需要先搬家。也避开了环：`team_bulletin` 不能 import 本模块。

相关：[[message_bus_trigger]]（cascade 深度）、[[team_summary_worker]]（触发计数与总结素材）

## 2026-08-11 — `trigger_label`

平台行成为触发消息时，指认行该怎么称呼它。**按类型分派而不是在调用处硬编码**：
原来那句 `"the team's Leader check"` 隐含「唯一会成为触发消息的平台类型是巡查」，
今天成立（停止通知与公告栏通知都是 `mentions=None`），但这条前提只写在注释里，
没有任何东西守住它——哪天另一种类型开始带 mentions，它就静默错了。
未知类型落到中性兜底，而不是把 `team_<id>` 当成队友名字印出来。

## 2026-08-13 — 第二、三种类型落地，分派表第一次被真的用上

两种无投递通知（[[delivery_notice]]）注册进 `PLATFORM_MSG_TYPES`，从三变五。

`trigger_label` 的预言应验了：`system_undelivered` 是**第一个带 mentions 的平台类型**
（A2A 沉默要叫醒提问方），也就是第一个能真正成为触发消息的类型。分派表为它加了
条目——这条注释当初写「the day another type starts carrying mentions」时等的那天，
就是今天。

顺带钉死了 [[team_summary_worker]] 的回归：它的 `_SYSTEM_MSG_TYPES` 与
`PLATFORM_MSG_TYPES` 对齐后，测试不再逐个重列类型成员，改比整个元组——
逐条列名正是这个测试本要消灭的那种手维护清单，只是上移了一层；它在 08-13 如期过期，
worker 早已正确、只有测试在说谎。

## 2026-08-12 — 再加两种：`system_cascade` / `system_roster`

见 [[team_notices]]。加进这个元组同时意味着它们被排除在总结触发、transcript 发言者渲染、
**和 agent 跳数**之外——最后一条对封顶通知是必须的：一条计入跳数的封顶通知会让上限越收越紧。
