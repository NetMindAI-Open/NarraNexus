---
code_file: src/narranexus/kernel/events/bus.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（二审修订）— 自有有界线程池 + 订阅者熔断

二审指出 `asyncio.to_thread` 用的是进程共享的默认线程池：被放弃的阻塞 handler 会一直占着线程，
坏插件能把全应用的 `to_thread` 饿死（平台自己成了打断源）。现在总线持有自己的
`ThreadPoolExecutor(max_workers=8, "nx-events")`，同步 handler 走 `run_in_executor`；`close()` 释放。
同时加订阅者熔断：某 owner 连续超时 `slow_threshold`（默认 3）次后不再投递（`EmitReport.suppressed`），
`reset_owner` 由工场页「重试」解除——熔断的是订阅者，永远不是回合。

## 2026-09-03（预审修订）— 同步 handler 走线程池；并发投递

预审指出：同步 handler 在协程里直接调用会卡住事件循环，`wait_for` 永远打不到超时（文档承诺的
「不能挟持回合」对一半的 handler 形态不成立）。现在同步 handler 经 `asyncio.to_thread` 执行，
超时后被放弃（线程继续跑完但不再阻塞回合，计入 `slow_counts`）；所有订阅者用 `gather` 并发投递，
最坏延迟是一个超时而不是 N 个。测试新增「阻塞 `time.sleep` 的同步 handler 被放弃」与「5 个
100ms handler 总耗时 < 0.4s」。

## 2026-09-03 — 进程内宿主事件总线（hooks kind 的底座）

平台在观察点 `emit(name, payload)`，插件 `subscribe(name, handler, owner)` 得到 `Disposable`。
三条设计决定：
1. **词表门**：只有 `contracts/events.HOST_EVENTS` 与 `declare` 过的名字能订阅/发射，拼错在订阅期
   `UnknownEntry`，而不是永远收不到（宪章 4）。
2. **隔离不吞**：handler 异常记 `error_counts[owner]` 并 warning，其余照送；总线本身绝不因插件
   而炸回合（铁律 #15「平台不能成为打断源」的另一面：插件也不能）。
3. **每 handler 超时**（默认 200ms，spec §10.2 的同步 hook 预算）：超时取消并记 `slow_counts`，
   `EmitReport.timed_out` 列出 owner，观察窗自动停用（批 2）靠这两张计数表。
`block(owner)` 一次摘掉某插件的全部订阅，与 `hooks.HookRegistry.block` 同语义。
批 0 只有总线与测试；平台各观察点接 `emit` 是批 2/3（D9）。与 `hooks.py` 的关系：hooks 是
「带返回值合成语义的调用点」（wrapper/firstresult），事件总线是「只通知的广播」；两者共享
错误隔离与 owner 归因的思路，但不共享实现，因为通知不需要 pluggy 的排序语义。
