---
code_file: src/xyz_agent_context/agent_runtime/background_run.py
last_verified: 2026-06-18
stub: false
---

## 2026-06-10 — `_finalize` 广播终结 `complete` 帧（live WS 唯一带内结束信号）

v1.0 的 WS handler 在 agent loop 返回后会
`send_json({"type":"complete"})`；Phase C 重构把这个帧弄丢了——
broadcaster 静默 close、server 关 WS，前端把这个关闭当成**被动断线**，
于是每一轮正常对话结束都会触发 auto-reconnect 机制（run_reconnect 重复
注入 user 气泡 + startStreaming 清空时间轴显示 "Starting up…" + 多个不
收敛分支让 isStreaming 卡死到手动刷新）。

修复：`_finalize` 在 step 3（terminal events row 写库）之后、
`broadcaster.close()` 之前 publish `{"type":"complete","state":...}`。
顺序刻意：前端收到 complete 后立即 refreshAgents，此时 DB 已是 terminal
state，不会再读到 stale 的 active_run。所有 terminal 路径（completed /
cancelled / failed）都经过 `_finalize`，所以 cancelled/failed 在各自的
专用帧之后也会跟一个 complete——前端 stopStreaming 幂等，安全。

依赖 broadcaster 的同步投递语义（见 broadcaster.py.md 同日条目）。
测试：`test_finalize_broadcasts_terminal_complete_frame`。

## 2026-06-18 — WS path now admission-gated via `BackgroundRun.drive`

`drive()` previously bypassed the admission controller despite the slot
logic being present — the `async with AgentRuntime()` block was indented
only 1 extra space under the slot (valid Python, but logically outside it
by intent). Fixed: `AgentRuntime` context and its entire body (`async for`
loop + natural-end STATE_COMPLETED branch) are now cleanly nested +4 under
the slot at proper 4-space intervals. The natural-end block (`self.state =
STATE_COMPLETED` + `_fire_message_success`) moved from 12-space indent to
16-space, placing it inside the slot but outside the AgentRuntime context —
slot is held until the run result is committed, not just until the
AgentRuntime generator drains.

Patch target for the lazy import: `drive()` does
`from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime`
which binds the name only in the local function scope. Tests that need to
stub `AgentRuntime` must patch
`xyz_agent_context.agent_runtime.agent_runtime.AgentRuntime` (the source),
not `xyz_agent_context.agent_runtime.background_run.AgentRuntime` (which
does not exist in the module's namespace).

## 2026-06-10 — `parse_db_utc` / `run_is_live` 共享心跳活性判定

原来住在 `backend/routes/auth.py` 的 `_run_is_live`（running 行只有在
heartbeat 3 个周期内新鲜才算活着）上移到本文件成为公共 helper——WS
reconnect 端点（websocket.py）需要用同一条规则区分「run 活在另一个进
程」和「run 死了但没写 terminal 行」。`RUN_STALE_AFTER_S =
HEARTBEAT_INTERVAL_S * 3`。只读判定，绝不据此停止/修改 run（铁律 #14）。

## 2026-06-09 — funnel: fatal-error turns are NOT a successful round-trip

A *fatal* `ErrorMessage` (e.g. `NoProviderConfiguredError` — the "configure
your key" notice) is yielded by AgentRuntime and the generator then returns
normally, so `drive()` still reaches the natural-end `STATE_COMPLETED` branch.
But the user received an error notice, not a genuine reply — so
`message_round_trip_succeeded` must not fire. `emit()` sets `self._had_fatal_error`
when it sees an error event whose `severity` is `fatal` (the default); the
natural-end branch gates `_fire_message_success` on `not self._had_fatal_error`.
`recovered` / `recovered_after_reply` (a reply WAS delivered) and `recoverable`
(transient; loop continues) are deliberately NOT treated as fatal — they remain
successful. This is an analytics-accuracy gate only; run state, error display,
and DB persistence are unchanged (state is still `STATE_COMPLETED`).

## 2026-06-08 — funnel: message_round_trip_succeeded on COMPLETED

Added module-level `_fire_message_success(user_id, agent_id, run_id)` helper
and called it right after `self.state = STATE_COMPLETED` on the natural-end
branch of `drive()`. Does NOT fire in cancelled or failed branches, and not
in `_finalize` (which covers all terminal states). Additive instrumentation.

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
