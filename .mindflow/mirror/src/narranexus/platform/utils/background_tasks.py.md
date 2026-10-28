---
code_file: src/narranexus/platform/utils/background_tasks.py
last_verified: 2026-08-14
stub: false
---
# background_tasks.py — 脱离任务的唯一入口

## 为什么存在

血泪教训 #2 说 `asyncio.create_task(coro)` 无人持有时是雷，但它其实是**两颗**雷，
而全仓的修法一直不统一：

1. **任务会消失**。事件循环只持弱引用（`asyncio.all_tasks` 就是弱集合），挂在 await
   上的 task 可能被回收，不报错、不打日志，活儿就是没干完。实践中被 await 的对象通常
   会顺带把 task 拉住，所以这个失效模式**罕见、且事后无法调试**——正因如此它值得从
   设计上消灭，而不是每处单独推理"这里应该不会"。
2. **异常被推迟到 GC**。未取回的异常只在回收时以 warning 形式冒头，既不在出事的时刻，
   也常常不在同一个日志文件里。

改造前全仓有四处裸调用：`agent_runtime`（Step 5-6 后台钩子）、`hook_manager`
（依赖链回调实例）、`narrative/updater`（LLM 摘要更新）、`db/dataloader`（批量派发）。
`message_bus_trigger._dispatch` 是唯一做对的，它手写了 `add_done_callback`——本模块
就是把那份做法提成公共设施，省得每处再抄一遍、抄漏一半。

## 上下游关系

上游：上述四个调用点，经 `spawn(coro, name=...)`。`name` 是任务死掉时**唯一**能把它
认出来的字符串，务必可 grep。

下游：无。只依赖 asyncio 与 loguru，不碰 DB、不碰 schema——因此 `utils/db/dataloader`
反向 import 它不会成环。

`pending()` / `drain()` 是给测试的抓手。此前 `agent_runtime` 的后台钩子没有任何句柄，
测试只能 sleep 然后祈祷；`tests/agent_runtime/test_post_turn_hooks_background.py`
依赖 `drain()` 才写得出来。

**两者都按当前事件循环取范围**（loop-scoped）：只报告 / 只等待 `t.get_loop() is
asyncio.get_running_loop()` 的 task；不在 running loop 里调用（同步调用方）返回空集。
生产是单个长驻 loop，这条不体现；但 `spawn` 现在挂在 `DataLoader._schedule_dispatch`
上，pytest-asyncio 又给每个测试一个新 loop，所以过滤是必需的——见下面「坑」。

## 设计决策

**这不是 supervisor**。不重试、不重启、不定时取消。一个脱离的钩子跑一小时是合法负载
（铁律 #14），这里唯一修的是**我们自己把它弄丢**的能力。

**取消不算失败**。`_on_done` 遇 `task.cancelled()` 直接返回：取消是关停的正常路径，
把它报成 ERROR 会让每次干净停机看着像事故（教训 #3：常态就响的告警等于没有告警）。

**领域失败仍归调用方**。凭据告警、owner inbox 通知、按模块隔离，这些都留在协程内部的
try/except 里（`agent_runtime._run_hooks_background` 就是这么做的）。本模块只保证任务
不会丢、不会静默死。

**`drain` 有界**。一个卡死的脱离任务不能把测试 teardown 或关停路径变成挂起；超时后不
取消任何东西，仍在跑的继续留在追踪集合里。所以 `drain` 返回**不代表**活干完了，在意的
话自己查 `pending()`。

## 坑

`_TASKS` 的规模在**同一个 loop 内**由并发数决定而非累计数——done-callback 会把自己
摘掉。但 callback 是**被调度**执行的、不与 `await task` 同步发生，所以 `await task` 之后
立刻断言 `pending()` 会读到旧值；先 `await asyncio.sleep(0)` 让出一轮。测试里踩过。

**跨 loop 则不然**：loop 关闭时尚未完成的 task，其 done-callback **永不执行**，会永久留在
`_TASKS` 里。`pending()` 把它们**过滤掉**（而不是摘除），所以它们不会污染别人的断言，也
不会让 `drain()` 白等满超时——但集合本身仍会随死 loop 数缓慢增长。生产无此情形（单 loop）；
测试进程里按整轮 pytest 累计，当前规模无害。`tests/utils/test_background_tasks.py::
test_a_task_from_a_closed_loop_is_neither_reported_nor_waited_on` 用一个线程造出这种
"幽灵"来锁住这两条性质。

`_on_done` 里的 `_TASKS.discard(task)` 必须保持**无条件**——加了过滤，被过滤掉的 task 就
再也摘不掉，集合会从「按并发数有界」退化成「按累计数无界」。
