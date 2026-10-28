---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/harness/steering.py
last_verified: 2026-08-24
stub: false
---
# harness/steering — 插话入口

DRAIN_STEERING 调用点 day-1 在。两个实现:`NullSteeringInlet` 恒空(默认,测试锁定);`QueueSteeringInlet` 是 P4 TriggerInbox 落地——背一个进程内 asyncio.Queue,transport 层(本地 runner stdin reader / 云端 executor steer 端点)是唯一写入者,loop 只在 step 边界 `drain()`,producer(team/单聊)永不触碰 loop。drain 是快照即清空、绝不阻塞(空 inlet 是常态);顺序=队列 FIFO。注入只许纯追加(C2,护 prompt cache 前缀)。此文件只管 inlet;喂队列的 transport 与融合路由在别处(§见 live-steering 设计)。

## 2026-08-21(补)— 写入端契约:back-pressure 在 steer_inbox,不在这条 queue

QueueSteeringInlet drain 的那条 queue 是**已准入消息的在飞交接**,transport(`SteerChannel`)**留它无界**、`put_nowait` 从不阻塞;有界/back-pressure 落在上游 `steer_inbox` 写边界(producer 超速被 `SteerInboxFull` 挡,绝不丢,铁律 #16)。防这条在飞 queue 涨爆的不变量是 **orchestrator 的**:必须按 loop 的 drain 速率(一个 step 边界的量)往 channel 推,不能一次把 inbox 积压全 drain 进来——这条节奏由 steer 路由 PR 保证。

## 2026-08-23(补)— drain 剥 _steer_id 并累积消费 id

`QueueSteeringInlet.drain` 现在 `pop(STEER_ID_KEY)`([[model.py]]):**剥**掉平台记账键(模型请求里绝不出现)、
把它累积进 `_consumed_ids`;`take_consumed()`(loop drain 后调一次)返回并清空。这是「游标随真消费前进」的消费端:
loop 据此发 `TYPE_STEER_CONSUMED`,最终让 producer 只对**被 run 读到**的行推游标(见 [[message_bus_trigger.py]] 补5)。
无 id 的消息(测试/无 id producer)不累积。`NullSteeringInlet.take_consumed()` 恒空;protocol 默认 `[]`。

## 2026-08-24(补)— `wait_for_input` 阻塞孪生 + `_take_one` 共享剥离

新增 `SteeringInlet.wait_for_input(timeout, cancel)`——`drain` 的**阻塞孪生**:loop 只在 agent 显式调 `wait_for_input` 工具那步调它,普通边界仍走非阻塞 `drain`。两种结果(Owner 契约):非空 list(来料)或 `[]`(超时/取消)。
* **`NullSteeringInlet.wait_for_input` 立刻返 `[]`**——无 producer 能喂它,等只是白烧 timeout;单测 `wait_for(0.1)` 锁定它绝不阻塞。
* **`QueueSteeringInlet.wait_for_input`**:先 `drain` 快路径(已有货就不等);否则复用**一个** `queue.get()` task,按 `_WAIT_CANCEL_POLL_S=0.1` 切片 `asyncio.wait`,使取消在 0.1s 内被响应而非只在 deadline。复用单 get(而非每片 `wait_for(queue.get(),片)`)闭掉经典竞态:超时的 get 会把已出队的项丢掉;这里 pending get 只在**从未完成**时才 cancel,消息要么被返回、要么留在队列,绝不被吞(铁律 #16)。
* **`_take_one(msg)` 共享助手**:`drain` 与 `wait_for_input` 都过它——`pop(STEER_ID_KEY)` 剥键 + 累积 `_consumed_ids`,所以被 steer 进**等待中**的 run 同样推 producer 游标(消费语义一致)。`cancel` 结构鸭子类型在 `requested()`([[protocols.py]] `CancellationSignal`)。
* **deadline 补捞(2026-08-24 补)**:`remaining <= 0` 分支从 `return []` 改成 `return await self.drain()`。极窄窗口里 `put` 已 resolve getter future 但 `get_task` 尚未出队,消息留在队列;deadline 上补一次非阻塞 drain 立刻取走,而不是让 agent 先被告知「什么都没来」、下一步 DRAIN 才看到它(自相矛盾但自愈)。`get_nowait` 无 await 点,不与仍 pending 的 `get_task` 竞争;`finally` 再 cancel 那个 get(队列已空,不丢)。常态空→`[]`→照旧注入超时通知。回归 `test_..._does_not_lose_a_message_that_arrives_at_the_deadline` 改断言「got+drain 恰好一条」。
