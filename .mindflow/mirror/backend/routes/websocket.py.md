---
code_file: backend/routes/websocket.py
last_verified: 2026-07-15
stub: false
---

## 2026-07-15 — 用户 MCP 装配为 spec 形状（headers 随行）

DB 加载后组 `{name: {"url", "headers"?}}` 传 `pass_mcp_servers`（原
`pass_mcp_urls` 扁平 url dict）。日志只打服务名/数量，不打 headers 值。

## 2026-07-13 — Agent 实时层熔断器接入

fresh-run 路径加熔断器 `should_skip` 闸门：paused/cooling 时发一帧清晰的 `agent_circuit_open` error（severity fatal + cb_reason，**不静默**）并关 socket，不启动 run。帧文案由纯函数 `_circuit_open_frame(cb_reason)` 生成（reason→message 映射，可单测）。fail-open。


## 2026-06-11 — pass sender_user_id into trigger_extra_data

The chat WS now puts `sender_user_id = request.user_id` (the logged-in sender, JWT-validated) into trigger_extra_data, so the context builder can name the sender + derive is-owner (agent_runtime overrides ctx_data.user_id to the owner, dropping the original sender otherwise).

last_verified: 2026-06-10
stub: false
---

## 2026-06-10 — reconnect 对「僵尸 running 行」回 run_ended 而不是 warning

`_handle_reconnect` 在 `state=='running'` 且本进程 `active_runs` 没有
这个 run 时，原来一律发 `reconnect_warning` + 关 WS。前端把这个关闭当
被动断线 → 对同一个死 run_id 无限退避重连，spinner 卡死只能刷新。

现在先用共享的 `run_is_live(events_row)`（background_run.py 的心跳新
鲜度规则，和 agents 列表 active_run 过滤同一条）区分两种情况：

- **心跳已过期**（进程死在 `_finalize` 之前 / terminal 写库失败留下的
  孤儿行）→ 回 `run_ended(state='failed', error_message='Run lost…')`，
  前端正常 stopStreaming 收敛。只读判定，不改 DB 行（startup reconcile
  仍负责修正孤儿行）。
- **心跳新鲜**（run 真活在另一个 backend 实例上）→ 维持原
  `reconnect_warning` 行为。

## 2026-06-09 — funnel redesign: no analytics in websocket.py

`_fire_terminal_accessed` and its call site on the fresh-run path were
deleted as part of the lean-funnel redesign. `EVENT_TERMINAL_ACCESSED` is
no longer part of the funnel. `websocket.py` now carries zero funnel
instrumentation — the nearest surviving funnel event is
`message_round_trip_succeeded`, which is emitted by the background run layer
(not here).

## 2026-05-13 — Reconnect 协议带回用户输入（dedup-safe）

`_handle_reconnect` 在推 `run_reconnect` 元数据帧时多带两个字段：

- `input_content` — 从 `events.env_context` JSON 里取 `input` 键。
  这个 JSON 在 `EventService.create_event` 步骤 0 时写入，shape
  是 `{"input": <user 文本>, "timestamp": <iso>}`。已经是落库的
  数据，不需要 schema 改动。
- `input_timestamp` — `events.created_at` 的 ISO（用
  `_format_dt`）。

**为什么是 `created_at` 而不是 `started_at`**：
`ChatModule.hook_after_event_execution` 把 user 这条持久化进
`agent_messages` 时用的 `user_ts = params.event.created_at.isoformat()`
（chat_module.py:786）。前端 ChatPanel 的 dedup 走 `role:content`
+ ±300_000ms 窗口（SAME_MESSAGE_WINDOW_MS），如果时间戳基准不一致，
即便差几秒也只是窗口在兜底——但用同一个字段作基准让"reconnect 注入
的 user bubble"和"run 结束后从 agent_messages 拉回来的 user 行"
匹配精度最高（只差 DB INSERT 时 SQL `datetime('now')` 与 Python
`utc_now()` 之间的几个毫秒），不会出现双重 user 气泡。

env_context 解析失败（JSON 损坏、列空）走 `suppress + warn-log`：
reconnect 仍然继续，只是前端那一帧拿不到 input_content，user 气泡
那一行就缺一下——比把整个 reconnect 路径搞挂好。

## 2026-05-13 — Phase D backend: force_stop 协议

`_listen_for_stop` 增加 `{"action":"force_stop"}` 分支——前端在用户点
graceful stop 后 10 秒没看到 cancelled 时弹"强制结束"，确认后发这条。
后端立即推 `{"type":"stopping","stage":"received","force":true}` ACK，
然后 cancel token；SIGKILL 实际由 Phase A C2 在 xyz_claude_agent_sdk
disconnect 5 秒超时后的 `process.kill()` 完成。

注意：force_stop 仍**走 finally / events-row 持久化**，state 写入
`cancelled` + reason='User force-stopped (escalation)'。我们不绕过
BackgroundRun 的清理路径——bypass 会留下 stale 内存对象 / 缺失的
events terminal 行。"force" 在协议层指"用户提速 + UI 显示 force
状态"，不是"硬杀 Python 内部状态"。

## 2026-05-13 — Phase C: agent 跟 WS 解耦 + reconnect 支持

websocket_agent_run 大重构：

**L1 — WS 断不杀 agent**

`_listen_for_stop` 在 `WebSocketDisconnect` 时仅 log，**不再**调
`cancellation.cancel()`。WS 断只代表"用户关 tab"，agent 该继续跑。
Only path that cancels is explicit `{"action":"stop"}` from a live WS.

**L2 — agent_runtime 跑成 BackgroundRun task**

Fresh run 模式不再 `async for message in runtime.run(...)`，改成：
1. 创建 BackgroundRun + cancellation
2. `asyncio.create_task(bg.drive(...))` —— task 自己 own AgentRuntime
3. `await bg.ready_event.wait()` 等到 step 0 yield 出 event_id、
   BackgroundRun 完成自我注册
4. 推 `{type: run_started, run_id: ...}` 给前端
5. subscribe broadcaster + `async for event in subscriber: ws.send_json`
6. WS 断 → `bg.broadcaster.unsubscribe()`；**不 cancel bg.task**

**Reconnect 模式 (新增)**

`AgentRunRequest.run_id` 字段设了即触发 reconnect 分支
`_handle_reconnect()`：
1. SELECT events row by event_id；user_id 必须匹配（防越权）
2. 推 `{type: run_reconnect, run_id, state, started_at, ...}` 元数据
3. SELECT event_stream rows ORDER BY seq ASC，逐条推
   `{type: replay, kind, seq, payload}`
4. state != running → 推 `{type: run_ended, final_output}` 关闭 WS
5. state == running + active_runs 有该 run → subscribe broadcaster + 
   forward live events
6. state == running 但本进程 active_runs 没有（在另一台 backend
   instance）→ 推 `reconnect_warning`、关 WS

**协议变化**

新增 ws inbound 字段：`run_id`（Optional，触发 reconnect 模式）
新增 ws outbound type：
- `run_started` —— fresh run 启动后立即推 run_id
- `run_reconnect` —— reconnect 模式入口
- `replay` —— history 回放
- `run_ended` —— terminal state + final_output
- `reconnect_warning` —— run 在其他 backend 实例上活着，本连接拿不到 live stream
- 之前已有：`stopping`, `cancelled`, `error`, `heartbeat`, `complete`, ...

## 2026-05-13 — Stop 三段 ACK 协议（Phase A C3）

用户点 Stop 后到 agent 真停的延迟可能从亚秒到几秒（取决于此时在 LLM
stream / tool call / cleanup 哪个阶段）。之前 UI 在这段时间是哑的——
按了等回应，体验=卡死。

加入三段 stopping 消息协议：

1. `{"type":"stopping","stage":"received"}` —— `_listen_for_stop` 收到 stop action
   立即推。给前端"我们听到了"的瞬时反馈
2. `{"type":"stopping","stage":"cleanup"}` —— `websocket_agent_run` catch
   `CancelledByUser` 时推。语义：cleanup（disconnect Claude CLI 等）已经完成
3. `{"type":"stopping","stage":"complete"}` —— 紧跟 cleanup 推。终止
   终态信号

也保留旧的 `{"type":"cancelled","message":...}` 给老前端兼容。

stage 2 和 3 实际上在代码层面是同步发出的（CancelledByUser propagate
到这层时 finally 已经跑完）—— 但保留三段是为了前端状态机干净，未来若
加 async post-cancel 工作也不需要改协议。

## 2026-04-21 更新 — WS 中途挂掉 + disconnect 诊断（Bug 32）

用户在 Web 端聊天时反馈 "工具调用到一半挂了，显示 not response"。根因不在这个文件——是 `stacks/narranexus-app/compose.yml` 的 uvicorn 启动命令**没设** `--ws-ping-interval` / `--ws-ping-timeout`，走默认 20s/20s。高密度 delta 推送时 pong 可能错过 20s 窗口，uvicorn 以 close_code=1011 硬断。

本文件侧相关调整：
- `_listen_for_stop` 在捕获 `WebSocketDisconnect` 时记录 `code` + `reason`，docstring 罗列 1000 / 1001 / 1006 / 1011 各自含义。下次再有类似 issue，运维从后端 log 一眼就能看出是浏览器关、代理砍、还是服务端 ping_timeout，不用再从前端反查。
- 修复的具体 uvicorn 参数改在 compose.yml / deploy-cloud.sh / dev-local.sh / main.py 四处同步（iron rule #7 双运行方式对齐）。

详见 BUG_FIX_LOG Bug 32。

# routes/websocket.py — Agent 运行时 WebSocket 流式通信

## 为什么存在

Agent 执行是一个需要几秒到几分钟的流式过程，期间会产生 thinking、tool call、agent response 等多种消息。HTTP 请求/响应模型无法处理这种场景，必须用长连接的流式协议。这个文件实现了 `/ws/agent/run` WebSocket 端点，是前端和 `AgentRuntime` 之间的实时通信桥梁。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(websocket_router)`（无前缀，WebSocket 路径直接是 `/ws/agent/run`）；前端聊天界面
- **依赖谁**：
  - `AgentRuntime` — 核心编排器，`async for message in runtime.run(...)` 产生流式消息
  - `CancellationToken`、`CancelledByUser` — 用户取消机制
  - `MCPRepository` — 加载 Agent 配置的 MCP URL
  - `backend.auth._is_cloud_mode`、`decode_token` — WebSocket 层的 JWT 验证
  - `backend.config.settings.ws_heartbeat_interval` — 心跳间隔（默认 15 秒）

## 设计决策

**双任务并发模式（Task A + Task B）**

WebSocket 端点同时运行两个 asyncio 任务：Task A（主任务）在 async for 循环里消费 `runtime.run()` 产生的消息并发送给客户端；Task B（`_listen_for_stop`）持续监听客户端发来的 `{"action": "stop"}` 消息。两个任务共享一个 `CancellationToken`，Task B 触发 cancel 后 Task A 的下一轮迭代会感知到并优雅退出。这个模式解决了"单向流式输出期间如何响应客户端取消信号"的问题。

**WebSocket 层做 JWT 验证而不依赖 HTTP 中间件**

浏览器 WebSocket API 不支持设置自定义 Header，所以无法用 `Authorization: Bearer ...` 传 token。中间件对 `/ws/*` 路径豁免，改由端点自己在第一条消息 payload 里读取 `token` 字段并用 `decode_token` 验证。额外做了 `token_user_id != request.user_id` 的比较，防止一个合法用户冒充另一个用户运行 agent。

**心跳任务防止代理超时**

很多反向代理（nginx、AWS ALB）在没有数据传输时会关闭空闲的 WebSocket 连接（通常 60 秒）。心跳任务每 `ws_heartbeat_interval`（默认 15 秒）发送一个 `{"type": "heartbeat"}` 消息，保持连接活跃。这对于 Agent 在思考过程中长时间没有输出的场景特别重要。

**消息序列化的多种 fallback**

`AgentRuntime` 产生的消息可能是各种类型，代码里有三种序列化尝试：`to_dict()`、`model_dump(mode='json')`、直接当 dict。这是为了兼容核心包里可能存在的不同消息类型实现。

## Gotcha / 边界情况

- **RuntimeError 吞掉而非抛出**：发送消息时如果 WebSocket 已经关闭，会抛 `RuntimeError`。代码里 catch 这个错误并 break 出循环，不是真正的异常。这是正常的连接关闭处理，不是 bug。
- **取消后 stop_listener 的清理**：即使 Agent 正常完成（不是取消），也需要 `cancel` 掉 stop_listener 任务，否则它会挂在那里等待一个永远不会到来的 stop 消息。`finally` 块里做了清理。
- **用时日志**：代码里有 `_ws_start` 和 `_step3_end` 时间戳用于日志里输出 `total` 和 `post-stream (step 4)` 耗时，`step 4` 是 Agent 发完最后一条响应后 `AgentRuntime` 继续执行的时间（比如写 Memory、更新 Narrative），这有助于分析性能瓶颈。

## 新人易踩的坑

WebSocket 连接建立后，第一条消息必须是完整的请求 payload（`agent_id`、`user_id`、`input_content` 等），不是 HTTP query param。如果前端连接后先发其他消息（比如心跳），请求解析会失败，连接会被关闭。

`/ws/ping` 是一个简单的 ping/pong 端点，只用于连接测试，不参与 AgentRuntime。
