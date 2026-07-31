---
code_file: src/xyz_agent_context/agent_runtime/run_recorder.py
last_verified: 2026-07-31
stub: false
---

# run_recorder.py — run 可观察性的持久化半身（唯一事实源）

## 为什么存在

2026-07-31 之前，「run 可观察」是聊天 WS 的私有功能：只有 BackgroundRun
驱动的 run（WS 聊天 / openai-compat）会逐条写 `event_stream`、维护
`events` 行状态机和心跳；所有 trigger run（lark / team bus / job / A2A）
是黑箱。Owner 拍板把可观察性升级为**平台属性**：任何 run 留下同一份
live trace，任何读侧（聊天重连、team roster、未来 dashboard）走同一条
路观察。本文件就是那份契约的唯一事实源。

## 内容

- **run 状态机**：`STATE_RUNNING/COMPLETED/CANCELLED/FAILED` +
  `HEARTBEAT_INTERVAL_S`（30s）+ `RUN_STALE_AFTER_S`（3 拍）。从
  background_run.py 移入 —— 状态机属于持久化层，不属于 WS 传输层。
- **`run_is_live`**：读侧唯一判活口径（agents 列表、观察端点、清扫
  共用）。只读，绝不据此停 run（铁律 #14）。
- **`sweep_stale_runs`**：把心跳停跳的 running 行翻 failed。**按心跳
  判，不按进程判** —— run 可能活在别的容器里，「本进程不认识」证明
  不了死。backend lifespan 启动时 + 周期性调用。
- **事件规范化**：`normalise_event` / `event_to_wire` /
  `try_extract_event_id`（从 background_run 移入并去下划线公开）。
- **`RunRecorder`**：消费一个 run 的规范化事件流，写 `event_stream`
  行（組合 B 粒度：thinking 整段一行、tool_call/tool_output/
  text_delta/progress 各一行）+ `events` 行生命周期字段 + 心跳 task。
  `record()` 首个 Step-0 progress 帧 late-bind run_id；`finalize()`
  幂等收尾。两个可选回调：`on_run_id`（传输层注册）、
  `on_thinking_buffer`（Broadcaster 的段内快照镜像）。

## 设计决策

- **观察者纪律**：每个 DB 写独立 try/except —— 记录失败绝不弄坏被
  记录的 run。progress 帧也持久化（观察者要能回放 0~3 步流水线）。
- **杀开关** `NARRANEXUS_RUN_RECORDING_DISABLED` 只管 trigger 挂载面
  （client.py）；BackgroundRun 的持久化早于此开关存在，聊天重连依赖
  它，不受影响。
- seq 每 run 单 writer（per-run 单 recorder 实例），单调无竞争 ——
  观察端点的 tail-follow 依赖单调性。
- `finalize(completed)` 只在 `events.final_output` 为空时回填
  （step_4 的写入是权威）。fatal-completed run（死 key 自然结束）
  **不**写 error_message —— completed 行上盖错误会显示假故障，
  错误已作为 error stream 行存在。

## 上下游

被 `background_run.py`（组合）与 `agent_runtime/client.py`（trigger
挂载）消费；读侧是 `backend/routes/websocket.py` 的观察端点与
`backend/routes/auth.py` 的 active_run 富集；`backend/main.py`
lifespan 调 `sweep_stale_runs`。

测试：tests/agent_runtime/test_run_recorder.py。
