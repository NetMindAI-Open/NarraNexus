---
code_file: tests/channel/test_ingress_breaker_audit_trail.py
stub: false
last_verified: 2026-08-28
---

## 2026-08-28（接线 review）— 又两条

**清扫的 channel 作用域**。去掉作用域时全套测试**照样绿**，而它正是这一轮
最实的一条缺陷——保留期是按 trigger 声明的类属性，不带作用域就变成「谁先跑
谁说了算」，且失效方式是静默丢数据。

**事件名只有一份**。参数化跑 `transition` 全部取值断言 `audit_event()` 的映射，
再加一条 grep：两个调用方都不许出现那三个事件常量——重新长出一个
`if transition == ...` 时，它连编译出一个名字来比较都做不到。

## 2026-08-28（接线 review 二轮）— 名字要说实话，范围要收窄

`test_both_surfaces_read_the_same_event_from_the_verdict` 改名
`test_the_verdict_owns_the_event_mapping`：它构造 verdict 直接调
`audit_event()`，**两个面一个都没跑**。真正保证两面一致的是那条源码级检查，
名字claim 了它没有的端到端覆盖。

那条源码级检查从「整个模块」收窄到**两个写入函数**：`/healthz` 计数或某段
docstring 正当地点名 `ingress_breaker_tripped` 时，原来的范围会以「映射正在
漂回调用点」的理由拦下一个与映射无关的改动，而报错信息把人引向错的方向。

新增穷尽断言：`INGRESS_TRANSITIONS` 每个成员都要映射得到事件（见
[[ingress_guard.py]]）。
# test_ingress_breaker_audit_trail.py — 铁律 #16 的那道门槛

## 为什么单独一个文件

熔断生效之后，被判为风暴的入站消息会被丢弃，而**当事人看不到任何信号**——
表现就是「助手不说话了」，也就是 0802 的症状。8/14 那次跑满 70 小时无人发现，
根本原因就是「机器人怎么安静了」这个问题**没有任何持久记录能回答**：日志轮转
了，DB 里什么都没有。

所以这条路径的规则是：**每丢一条消息写一行**，且行里要能答出「为什么」和
「到什么时候」。这不是可观测性的锦上添花，是铁律 #16 的准入条件——拆分方案
明确要求它与四个挂载点同 commit，不能作为跟进项。

## 为什么原生面和托管面各要一份

两个面**走不同的审计调用点**：原生走
[[channel_trigger_base.py]] 的 `_audit_ingress_verdict`，托管走
[[managed_channel_ingress.py]] 自己的直写 seam（协调器刻意不经过 trigger，
因为有两条 deny 路径恰恰是在 trigger 不可用时触发的）。

这一点是被变异抓出来的：把原生那侧的 drop 审计改成不写，托管面的两条用例
**照样全绿**。只测一个面等于漏掉一半。

## 构造上的两个坑

- **guard 由 `start()` 构造**，而这个文件直接驱动 `_process_message`。要用
  `build_ingress_guard(db)` 这个工厂造，不能手搓一个——手搓的话钉住的是一个
  与线上配置不同的 guard。
- **被放行的消息会继续走完整管线**。子类里把 `_build_and_run_agent` 短路掉：
  本文件的主题是被**拒绝**的那些消息，让放行的那几条拖进整个上下文管线只会
  让失败原因变得难读。

## 断言的是「每条一行」，不是「每次跳闸一行」

一次跳闸一行能回答「门关了」，但答不出「关了多久、吞了多少」。所以断言
`message_id` 互不相同——「我发的哪几条没送到」这个问题必须能从行里数出来。
