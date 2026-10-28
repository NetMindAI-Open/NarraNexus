---
code_file: src/narranexus/platform/utils/temporal_guard.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-18 (review 修正) — 审计写入改走 ServiceAuditRepository

原来手写 `db.insert("service_audit", …)` + `json.dumps` + `try/except` 三件套。全仓所有
`service_audit` 写入方都走 `ServiceAuditRepository.record` 或 `services/service_audit`，
只有这里是例外——分层规则（repository 负责 CRUD）在这没有例外理由。

功能当时是对的，代价在演进：`service_audit` 写入形状变化时 repo 改一处，这里不会跟着动。
两边序列化参数也不一致（repo 用 `default=str` 兜底，这里没有），同样的 detail 可能一个
成功一个丢。

`record()` 自己就是 best-effort 且永不抛，所以本文件那层 `try/except` 和 `import json`
一并删掉，函数更短。`test_audit_write_failure_is_swallowed` 的语义因此变成了**端到端保证**
而不是本文件的 try/except——已在 docstring 里写明，否则下一个人会误读这条测试在验什么。

# temporal_guard.py

## 为什么存在

我们给 agent 加了时间工具和提示词规则，但没有任何办法知道**这些手段到底有没有生效**。
"agent 把日期说错了" 一直只能靠用户投诉进来，一次一条，抽样偏差极大 —— 没人抱怨不代表
没发生，只代表这次没人较真。

这个文件就是那个缺失的观测点：它读 agent **已经发出去**的回复，把 "今天是 X" 这类对
当下日期的断言跟真实时钟对一遍，不一致就往 `service_audit` 记一行。

对应 CLAUDE.md「事故经验」§4 的 L3（端到端业务可观测）和 §5（DB 里的业务事件 > 应用日志）：
日志会被 rotate、会被 `docker restart` 冲掉、grep 到不到取决于谁挑的关键词；DB 里的一行
是结构化的、可以 SQL 的、能回答"最近 14 天这类错误出现了几次"的。

## 它明确**不**做什么

不改回复、不拦截、不重新提示模型、不影响 `agent_loop` 的任何决策。

这是设计约束不是实现偷懒：

- 铁律 #15 —— 平台不管用户选了什么模型，不因为模型表现"不合适"就介入它的行为
- 铁律 #16 —— 资源/质量问题不能用"用户能感知到内容损失"的方式解决

一个会改写模型输出的过滤器同时踩了这两条。而且从时序上讲也来不及：它挂在 step 4.8，
消息早就送到用户手里了。

所以分工是明确的：**提示词和工具负责预防，这个文件负责诚实地报告预防失败的频率。**

## 只查直接断言

只匹配对"现在"的直接断言 —— `今天是 8 月 7 日` / `现在是 2026-08-07` / `Today is Friday`。
这类句子对不对，只需要跟一个数字比一下，没有解释空间。

**刻意不做**泛化匹配（"活动快到了" / "那是下周的事"）。判断这类说法对不对需要上下文，
产出的会是一串"可能有问题"。一个没人信的探针会被静音，而被静音的探针比没有探针更糟 ——
它在报表上看起来像是有覆盖。宁可窄而准。

同理，不存在的日期（2 月 30 日）跳过不报：我们量的是**比较错误**，不是模型的日历常识。

### ⚠️ 读这个指标前必须知道：事故原句本身不在覆盖范围内

线上那句是「**今天是活动日**」——有 today marker，但后面既没有日期也没有星期，四条正则
一条都不匹配。这是上面那个设计取舍的直接后果，不是遗漏。

所以：`service_audit` 里 `temporal_guard` 的计数量的是「**显式日期/星期断言**」这个子集，
**不是**「日期说错」这件事。连续 0 命中有两种可能——真的修好了，或者这类措辞根本不在射程内。
把它当成时间准确性的覆盖率读就会读错。

## 上下游

**被调用**：`agent_runtime/_agent_runtime_steps/step_4_persist_results.py` 的 4.8 段，
只在本轮确实向 owner 投递了消息时触发，文本来自
`MessageSourceHandler.extract_owner_visible_text`（跟 session anchor 用同一个真值来源，
所以两者对"用户看到了什么"的判断不会分叉）。

**依赖**：`utils/timezone` 的 `utc_now` / `to_user_timezone` / `resolve_timezone` /
`WEEKDAY_NAMES` —— 跟 agent 读到的 ground truth 走同一个渲染层，否则探针自己就会在
时区上出错，那就成了笑话。

**写入**：`service_audit`（`service='temporal_guard'`,
`event_type='date_claim_mismatch'`）。复用而不是新建表：字段形状正好合适，一个还没证明
自己价值的诊断不该同时要一次 migration。

## 坑

**中英文混排是常态**，不是边界情况 —— 生产回复里同一条消息里两种语言都出现。所以两套
正则都得维护，改一边忘另一边就等于只覆盖了一半流量。

**判断在用户时区做。** UTC 01:00 在上海是今天、在纽约还是昨天。这个搞反了的直接后果是：
所有跨夜回复全部误报，探针一天之内就废了。

**日志级别是 WARNING 不是 ERROR。** 从 runtime 的角度什么都没坏 —— 这一轮成功了，用户
也收到回复了。这是给我们看的正确性信号。级别开高了就会被调走，对应事故经验 §3。

**全链路 fail-open。** scan 抛异常吞掉、audit 写失败吞掉。一个报告用的探针如果能把它
报告的对象搞挂，那就没人敢在生产打开它 —— 而打不开的探针等于不存在。
