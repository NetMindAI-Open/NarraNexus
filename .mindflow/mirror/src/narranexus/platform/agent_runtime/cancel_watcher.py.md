---
code_file: src/narranexus/platform/agent_runtime/cancel_watcher.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #394 第二轮 review I-4）— 活性规则单一导入路径

`parse_db_utc` 改为从 `narranexus.platform.utils.run_liveness` 导入（原来经 `run_recorder`
转手），行为不变。

# cancel_watcher.py — 把「停止」送到持有 token 的那个进程

## 为什么存在

`CancellationToken` 是**进程内**的东西(基于 `asyncio.Event`)。单聊
stop 之所以能秒停,是因为前端、token、run 三者在同一个 backend 进程里:
`websocket.py` 的 `_listen_for_stop` 收到帧就地 `token.cancel()`,token
存活在 `app.state.active_runs`。

而群聊 run 由 **workers 进程**的 message_bus_trigger 驱动 —— HTTP 请求
永远进不去那个进程。这就是 2026-07-23 事故的机制底座:喊停 8 分钟没有
任何反应,不是因为停止慢,而是**当时从任何地方都停不掉一个总线驱动的
run**(调用 `run_and_collect` 时没传 cancellation,runtime 自己造了个
外界无法触发的 no-op token)。

本文件是跨进程的接收端:取消请求经 `events.cancel_requested_at` 落库,
watcher 在 run 所在进程读到它,触发本地 token。

## 为什么用 DB 当媒介

这是本仓库对「跨进程 run 事实」的既有风格,不是新发明:`run_is_live`
心跳、`sweep_stale_runs`、观察端点的 tail-follow 全都以 `events` 表为
中介,进程之间从不直接对话。备选方案「workers 开内部控制端口」输在
多进程拓扑、服务发现和鉴权复杂度上。

## 刻意不放进 RunRecorder

recorder 的契约是**纯观察者,永不影响被记录的 run**(铁律 #14 ——
`client.py` 的 `_RecordedRuntime` 注释把这句话写死了)。本类的职责恰好
相反:它就是来停 run 的。同一张表、相反的授权。合并两者等于把中断路径
塞进「保证自己从不中断」的组件里。

## 设计决策

- **单例 + 批量查询**:一个进程一个 watcher(`get_cancel_watcher`),
  持有 `{run_id: token}`。每 tick 是**一条**跨全部注册 id 的查询,而不是
  每个 run 一条 —— 驱动 N 个并发 run 的进程仍然是每秒一次往返。
- **只 SELECT 三列**。`events` 有 `event_log` / `env_context` 等
  MEDIUMTEXT,`get_by_ids` 会把它们整行拉进内存;每秒一次拉全行是白烧
  内存和带宽。
- **注册时机 = `on_event_id`**。run 要有 id 才有东西可键,Step 0 铸出
  event_id,`collect_run` 的 `on_event_id` 回调是它存在的最早一刻。不需要
  改 client 或 collect_run 的签名。
- **旗标是时间戳,判据是 `requested >= started_at`**。旗标活在长寿的
  events 行上,「有旗标」本身不够 —— 必须晚于当前 run 开始,否则上一轮
  快结束时落下的请求会杀掉它的后继。
- **`started_at` 解析不出时照样停**。用户明确要求停止,因为一个技术细节
  拒绝执行,就是在重演那 8 分钟黑箱。
- **空注册表就退休**。没有总线流量的进程不该永久持有一个每秒查询;
  下一次 register 会把 loop 拉回来。

## Gotcha

- **event-loop 亲和**:`CancellationToken` 包的是 `asyncio.Event`,只在
  创建它的 loop 内安全。poll task 和 token 必须同属一个 loop —— 今天所有
  调用方都满足(trigger 在跑 agent 的同一个 loop 里 register)。未来若有
  调用方在第二个 loop 上驱动 run,那个 loop 需要自己的 watcher 实例。
- **每条失败路径都降级为「没有待处理的停止」**,并保留注册表:DB 读不到
  意味着答案**未知**,而不是「没有」。这里绝不能因为 watcher 自己的坏运气
  去掐一个跑得好好的 run。
- poll task 用 `add_done_callback` 记异常 —— 裸 `create_task` 的异常只在
  GC 时报一句 warning(事故教训 #2),而这个 task 正是停止能否落地的关键。

## 上下游

- **上游**:`backend/routes/runs.py`(写旗标)、message_bus_trigger 的
  `_handle_channel_batch`(register / unregister)
- **下游**:`CancellationToken` → AgentRuntime 的检查点 → `CancelledByUser`
  → `client.py` 已有的 `finalize(STATE_CANCELLED)` 专线
- **兄弟**:[[run_recorder]] 的 `sweep_stale_runs` 读同一个旗标决定终态是
  cancelled 还是 failed

测试:`tests/agent_runtime/test_cancel_watcher.py`
