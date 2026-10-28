---
code_file: src/narranexus/platform/message_bus/wake_signal.py
last_verified: 2026-08-18
stub: false
---

# wake_signal.py — 跨进程的「有新活儿」提示

## 为什么存在

`MessageBusTrigger._wake`（2026-08-14）用一个进程内 `asyncio.Event` 关掉了两轮之间的
死气：A 发帖、B 被 @、B 干等一个自适应间隔（3–12s）才被发现。它自己的 docstring 写明了
覆盖范围与补法：*"Making it cross-process means a DB signal and a reader for it"*，并把
补法推迟到「peer DM 延迟真成为抱怨」的那天。

那天由**契约变更**提前到来：team 回复改成 MCP 工具（`message_team`）之后，房间自己的
接力**搬到了进程内 Event 从来覆盖不到的那条路上**。不做这个模块，就等于把
`c7739ad1` 量出来的延迟收益吐回去一部分——而且是以「房间安静了」的形式被用户感知，
正是铁律 #16 禁止的那类退步。

## 设计决策（按要紧程度）

**一行，不是队列。** 信号只说「现在去看」，从不说「去看 X」。轮询循环本来就知道怎么找
待处理的活儿；在这里复制那份知识 = 同一个问题有两个答案。

**挂在写入的那道缝上。** `LocalMessageBus.send_message` 是全仓**唯一**的 `bus_messages`
插入点，所以「发了帖没叫醒」从一条要记住的纪律变成**结构上不可能**——没有第二个插入点
可漏。这也让 [[message_bus_trigger]] 那条结构性守卫测试（禁止本模块内出现
`self._bus.send_message(`）失去存在意义：它守的是调用方会忘，而唤醒现在在写入里面。

**在 insert 之后 bump，不是之前。** 信号的含义是「有新活儿」；插入失败了还 bump，轮询
会醒来发现什么都没有，信号从此不再有意义。`test_a_send_that_fails_does_not_bump` 钉住。

**两个方言一份定义。** 和其它表一样登记在 `schema_registry`，所以桌面 DMG 与云端行为
一致（铁律 #7）。

**读取 fail OPEN。** 读不到就当「没有新消息」，退回调用方的定时器。为一个延迟优化把
poll 循环搞崩是本末倒置。但「行为上安静」不等于「不可观测」——能发现信号失效的是
`[bus-timing]` 里的 `queue_wait`（铁律教训 #4：只有 L1 存活检查是僵尸的后门）。

## 上下游

- **谁写**：[[local_bus]] 的 `send_message`（唯一写入点）
- **谁读**：[[message_bus_trigger]] 的 `_wait_cross_process_wake`，在
  `_sleep_until_due` 里与 stop、进程内 `_wake_event` 并列等待
- **切片长度**：`WAKE_SIGNAL_SLICE = 0.5s`，把「别的进程发了消息」的额外延迟压在一秒内，
  代价是每秒两次单行读

## 顺带修好的

`message_agent`（当时叫 `bus_send_to_agent`，prod 286 次 / 67 agent）**一直**在等轮询——它
从来是 MCP 工具，从来没有唤醒。这不是本次改造引入的问题，是本次改造顺带修掉的。

## 2026-08-18 — 基线必须在扫描之前取

跨进程唤醒原本在**睡眠入口**读基线，即待处理扫描跑完之后。MCP 服务器在扫描期间张贴的
`message_team` 会 bump 信号，睡眠者随后把那个新值当成自己的基线、去等一次**更进一步**的
变化 —— 消息于是干等一整个自适应间隔（3-12s），正是这套机制存在的目的所反。而且那是 bump
最可能落下的时刻，因为扫描是整个周期最慢的一段。进程内 `_wake_event` 没有这个洞：它在扫描
期间 `.set()`，只在睡眠结束时才清。

改为在轮询周期顶端由 `_snapshot_wake_baseline()` 取（[[message_bus_trigger.py]]），失败时
保留旧值 —— 陈旧基线会让下一次睡眠提前返回，代价是一次白跑的扫描，方向是安全的那侧。
守卫两条：扫描期间 bump 的行为测试（已变异验证）＋ 源码顺序断言（把读取搬回睡眠者是一行改动，
单测会全绿）。

## 2026-08-18 (二) — 单行热点：现在无害，但要记着它在哪

`bump` 在**每一次** `bus.send_message` 上写同一行，来源包括 trigger 进程和 MCP 服务器。当前
量级下无事：autocommit 让锁持有极短，且宽 `except` 会 fail open。但这在 InnoDB 上是一个全局
共享的行锁，落在发送路径上 —— 它是一个序列化点，而 SQLite 测试在结构上看不到这件事
（单进程、单连接）。

MySQL twin 覆盖的是**正确性**，不是并发。如果将来发送延迟出现回归，先看这里 ——
`[bus-timing]` 那行里的 `queue_wait` 是观测口（铁律经验 #4：L2 观测优于「进程活着」）。

`bump` 的 update-then-insert 依赖 MySQL 的 affected-rows 语义；同一微秒内的两次 bump 会撞
重复键，被 debug 级吞掉。实际不可达（同一行的两次写会被锁串行化），但这是它的行为而不是
它的保证。
