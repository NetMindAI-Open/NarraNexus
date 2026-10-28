---
code_file: tests/services/test_service_audit_write_outcome.py
last_verified: 2026-08-26
stub: false
---
# test_service_audit_write_outcome.py — 审计写入结果的链级覆盖

## 为什么单独一个文件

`ServiceAuditor.event()` 的返回值不是给日志看的：[[step_3_agent_loop.py]] 的
DM 兜底审计**据它 arm 一个 10 分钟冷却**。所以「True 意味着行真的落库了」是
一条被依赖的保证，而它要穿过三环——`event()` → `record()` → `db.insert()`
——**每一环都写成吞掉自己的异常**。任何一环吞了异常又不报告结果，上层的
「没抛 = 成功」就恒真，保证静默失效。

这个 PR 已经在同一条链上栽过两次：

1. `event()` 不报告成败，冷却按「没抛异常」arm —— 配套测试用一个**会抛的
   假 auditor** 钉绿，而真的 `ServiceAuditor` 永不抛，等于为一个只有假货才
   具备的性质写了注释、mirror 和测试。
2. 修完第 1 条之后，`record()` 仍然自己 catch 掉 insert 异常并返回 `None`
   ——「landed write」实际只覆盖「拿不到 db handle」。

两次的形状一样：**假货比真货诚实**。所以这里的用例不打桩 `record()`，而是
拿一个 `insert` 会抛的 db handle 去驱动**真的 `ServiceAuditRepository`**，
再拿真的 `ServiceAuditor` 套在外面，把整条链一次性钉住。

## 同时钉「仍然不抛」

报告结果和抛异常是两件事，容易在重构时被合并。最后一条用例专门断言：拿不到
db 时 `event()` 返回 False 而**不是**抛出去——观察者不能打断被观察者，这条
路径上每个调用方都依赖它。
