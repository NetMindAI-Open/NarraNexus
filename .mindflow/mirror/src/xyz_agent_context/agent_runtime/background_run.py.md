---
code_file: src/xyz_agent_context/agent_runtime/background_run.py
last_verified: 2026-05-13
stub: false
---

# background_run.py — agent_loop 跟 WS 解耦 + 持久化 + 广播

## 为什么存在

铁律 #14 说 agent run 是 first-class、可以跑几十小时——但之前的实现把
agent_loop 跟 WebSocket task 绑死，WS 一断 cancellation 立即触发，agent
当场死。

BackgroundRun 把 agent run 提到自己的 asyncio task（owned by
`app.state.active_runs[run_id]`），WS 退化为订阅器。关 tab → unsubscribe；
agent 继续跑。再开 tab + 带 run_id query → 从 DB replay event_stream +
重新 subscribe live broadcaster（如果 run 还活着）。

## Run lifecycle 由 events 表持久化

```
events.state:  running → completed | cancelled | failed
events.started_at | last_event_at | finished_at | tool_call_count |
                 current_stage | error_message
```

每 30 秒一次 heartbeat task bump `last_event_at`——给 reconcile 检测假死
+ 监控用。`current_stage` 跟随 step 切换更新。`tool_call_count` 在每次
tool_call event 自增。terminal state 由 `_finalize` 写入。

## Stream events 持久化到 event_stream 副表（組合 B 粒度）

per stream chunk 一行：

- `tool_call` —— 一个 row per call（payload 是 tool_name + arguments JSON）
- `tool_output` —— 一个 row per output
- `thinking_segment` —— **整段**一行（在 type 切换或 run 结束时 flush）。
  4408 个 raw thinking chunks → ~50 个 segment rows。`_current_thinking_segment`
  list 在内存里累积；`_flush_segment` 在 emit 看到非 thinking 事件时合
  并出整段写一行
- `text_delta` / `progress` / `error` / `other` —— 完整持久化

## run_id 的 late-binding

构造时 BackgroundRun 不知道 run_id；drive() 内部 `async for event in
AgentRuntime.run(...)` 拿到 step 0 完成 progress message 的
`details.event_id` 时，`_on_run_id_assigned` 把它绑到 self.run_id、
注册到 active_runs、启动 heartbeat、UPDATE events 行的 state→running。
Caller 用 `await bg.ready_event.wait()` 等就绪。

run_id 设置之前的事件（step 0 RUNNING 的那条 progress）只 broadcast，
不持久化——它们的逻辑归属是 step 0 的进度，没有对应的 events 行还。

## 关键不变量

1. 任意 terminal state → `_finalize` 调用 → 写 terminal events row +
   close broadcaster + 从 active_runs registry 移除 + set ready_event
2. `_finalize` 幂等——可重复调用
3. Run task 自己 owning AgentRuntime context manager（async with）——
   caller 不应该外部干预
4. WS 断开**不**触发 cancellation——只有显式 user stop 才 cancel

## Backend 重启 reconcile（main.py:lifespan）

进程启动时 active_runs registry 是空的。任何 `events.state='running'`
的行都是上次进程留下的孤儿——`main.lifespan` 跑一次 UPDATE 把它们改成
`state='failed', error_message='backend restarted, run lost'`。前端识别
这个状态显示给用户。

Spec: `reference/self_notebook/specs/2026-05-13-agent-runtime-lifecycle-and-stream-resilience-design.md` §4.1
