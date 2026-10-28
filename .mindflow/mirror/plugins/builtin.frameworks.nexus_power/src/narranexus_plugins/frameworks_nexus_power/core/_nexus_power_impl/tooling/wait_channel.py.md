---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/tooling/wait_channel.py
last_verified: 2026-08-24
stub: false
---

# wait_channel.py — `wait_for_input` 控制工具:agent 主动 HOLD 住 turn 等新输入

场景:team 房间某 agent 做完自己那部分、预期队友/主人马上回,它可以 `wait` 住 turn,新消息一到就立刻继续
(融合=立即,下一次 LLM 执行)。Owner 决策:只有两种结果——**有货**(等到)或 **超时**(到点没等到)。

## 机制(与 [[loop.py]] WAIT 边界配合)

`WaitChannel.call("wait_for_input", {seconds})` **不阻塞**:只把 clamp 后的 seconds 记进共享 `WaitState.pending`
(像 [[scheduling_channel.py]] 用 PlanState 的手法——channel 经共享态给 loop 发信号,不 reach 进 loop)。loop 在
非阻塞 drain 为空、STOP_CHECK 之前读 `a.wait.pending`,自己做阻塞等待(loop 才握 inlet 与 cancellation)。

- clamp:seconds ∈ [MIN=1, MAX=300],缺省/垃圾/NaN→DEFAULT=60(fail-soft,agent 想等就让它等)。clamp 有**唯一家**:`WaitState.request(raw)`——既 clamp 又存 `pending` 又返回 clamp 值给回帖文案。每个 producer(今天 WaitChannel、明天 executor /steer)白得,不各自重 clamp;loop 因此可信 `pending` 已在界内。锁在 `test_wait_state_request_is_the_single_clamp_home`。
- `WaitState` 每 turn 一个可变持有者、满足 `WaitRequest` 协议([[protocols.py]]);loop 在 DRAIN **之前**一次读清 `pending`(读一次性、不跨 step 残留,见 [[loop.py]] 补)。
- 只在 **`TurnOptions.steerable` 为真**的轮暴露该工具:纯函数 `_steer_channels(steerable, …)`(见 [[assembly.py]])。**不是**判 inlet 是否挂着——subprocess `runner.main()` 每轮都挂 `QueueSteeringInlet`,判身份恒真;steerable 是 orchestrator「是否注册了 `SteerChannel`」的决定,经序列化边界带过来。非可控轮挂上=在无人喂的队列上真阻塞满 clamp(不是即时超时)。

## 阻塞在 [[steering.py]] 的 `wait_for_input`

`QueueSteeringInlet.wait_for_input(timeout, cancel)`:queue 有货即时返回;否则复用**同一个** `queue.get()` task 按
`_WAIT_CANCEL_POLL_S` 切片等,cancel 可打断、且**不丢消息**(超时/取消只 cancel 尚未 dequeue 的 get,不会吞掉已取的);
有货后再非阻塞 drain 其余=一次等待可带回多条(融合)。NullSteeringInlet 即时 []。

## 与消费契约的集成(#352,已接)

本分支已 rebase 到含 #352 消费契约的 dev,集成**已实现**(非 TODO):`wait_for_input` 经与 `drain` **共用**的
`_take_one` 剥 `_steer_id`+累积 `take_consumed`(含阻塞时先到的 first——它绕过 drain,故 wait_for_input 单独 `_take_one`
它);loop 的 WAIT 边界在 `record_steering(waited)` 后 `take_consumed()` 非空则**直接 yield** `TYPE_STEER_CONSUMED`
(与 DRAIN_STEERING 同,不过 `_log`)。于是 steer 进**等待中**的 run 的消息也推 producer 游标、不会以新 turn 重投。
回归:`test_steering_inlet.test_wait_for_input_strips_and_tracks_steer_id_including_the_blocked_first`、
`test_wait_for_input.test_wait_consumption_emits_a_steer_consumed_event`。
