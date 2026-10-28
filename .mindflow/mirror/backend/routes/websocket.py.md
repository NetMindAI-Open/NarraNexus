---
code_file: backend/routes/websocket.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（PR #419 review I-3 / I-4）— 可见性闸的 local 语义与归属地

- **local / 桌面模式同样判定，不做 no-op。** local 下 `request.user_id` 已被上面的
  `?x_user_id=` 锚点校验保证非空且与 payload 一致，所以闸门照常执行：owner 通过、陌生人
  `AgentNotFound`（`test_local_mode_*` 两条钉住）。不采用 `_ownership._deny_reason` 的
  "无 user_id → 放行"：那个条件在这里永远不成立，硬加 `_is_cloud_mode()` 豁免会让 local
  端任何能连上端口的进程驱动任意 agent，且与重连分支的判据更难对齐。代价：`created_by`
  为空的历史行在两种模式下都无法再经 WS 启动，回的是统一 `AgentNotFound`。
- 闸门改为调用 [[_ownership.py]] 的 `assert_visible`（原 dashboard 的 `assert_agent_visible`
  已搬家）。DB 故障是非 HTTP 异常，穿过 `except HTTPException` 落到外层 `InternalError`
  帧——fail-closed，且不会被报成 not-found；这是 `_ownership` mirror 里写明的契约。

## 2026-10-28 — 新 run 先确认 agent 可见（归属或公开）

安全审计发现：握手只校验了 JWT 与 payload 的 user_id 一致，**从不检查 agent_id 是谁的**；
而 runtime 会把 user_id 改写成 agent 的 created_by，于是任何登录用户拿到别人的 agent_id
就能用主人的 workspace、工具、渠道和额度跑一轮。现在在 fresh-run 校验之后、熔断门之前
调用 dashboard 的 `assert_agent_visible`（自有或 is_public），失败统一回 `AgentNotFound`
错误帧并关闭——与 dashboard 同口径，不暴露 agent_id 是否存在。公开 agent 仍可被陌生人
驱动，这是现有"访客聊天"语义，未改。重连分支早已有 owner 检查。

## 2026-09-23 - Own runs before Step 0

Fresh runs start through `app.state.run_tasks`; this retains detached execution
even after WebSocket teardown and before its run ID is assigned. Shutdown drains
the task through its finalizer. `active_runs` remains the reconnect/control index.
The circuit-breaker probe transfers only once `bg.task` exists: construction alone
does not transfer ownership if shutdown refuses the task start.

## 2026-09-10（PR #394 第二轮 review I-4）— 活性规则单一导入路径

`run_is_live` 改为从 `narranexus.platform.utils.run_liveness` 导入（原来经 `background_run`
转手），行为不变；文中「background_run.py 的心跳新鲜度规则」现住在 `utils/run_liveness.py`。

## 2026-09-10 — fresh-run 路径两步熔断门，赢得的探测随 run 走

[[circuit_breaker]] 的 `should_skip`（纯读，返回 `GateVerdict`）放行之后、
`_record_message_accepted` 之前调 `try_begin_probe(agent_id, prior=cb_gate)`（复用同一次
读，普通 turn 只读一次，PR #394 M1）；被拒（`reason="probing"` 等）就发同一种
`agent_circuit_open` 帧并关 socket，run 不记录、不创建。赢得认领时
`cb_admission.probe_token` 交给 `BackgroundRun(probe_token=...)`，由 run 在终态按 token
结算（PR #394 I1）。认领之后、`bg` 建出来之前抛异常（会话登记、消息受理记录等）时，外层
`finally` 按 token `release_probe` 归还；`bg` 已存在则不归还——run 持有 token，此时归还会在
活探测下误重挂。`_circuit_open_frame` 的文案改由 `circuit_breaker.describe_skip_reason`
提供（与 [[openai_compat]] 共用一份）。锁：
`test_ws_claims_the_half_open_probe_before_recording_the_run`、
`test_ws_hands_back_a_won_probe_when_setup_throws_before_the_run`。

## 2026-09-09 — `_circuit_open_frame` 补上 `should_skip` 的第四个原因 `probing`

[[circuit_breaker]] 新增半开态 PROBING 后，`should_skip` 现在会返回
`(True, "probing")`（并发 turn 撞上正在进行的探测时）。`_circuit_open_frame`
的 else 分支本就把它当 "cooling" 文案处理（"稍后重试"，语义上完全对——探测
不是硬暂停，不该建议用户去重新登录），代码行为无需改；只是补全文档字符串
把 `probing` 列进已知的 `cb_reason` 取值集合，避免读者以为还是三值。
`test_frame_probing_falls_back_to_cooling_copy` 钉住这个回退行为。

## 2026-08-24 — `_format_dt` 补上时区契约(#349 I1 根因)

MySQL DATETIME(6) 剥 tzinfo → driver 还回 naive datetime → 原来的
`isoformat()` 吐无后缀串,浏览器 `Date.parse` 按**本地时区**解——
run_reconnect 帧的 `started_at`/`input_timestamp` 在云端整体偏移观看者
的 UTC offset(SQLite 往返自带 `+00:00`,本地永远复现不出)。naive 值
现在补 `timezone.utc` 再 isoformat。**不能**换 `format_for_api`:它截到
整秒,而 `input_timestamp` 与落库聊天行的去重依赖毫秒级一致(帧注释
明写)。前端侧的容错解析器是 [[../../frontend/src/lib/backendTs]]。

## 2026-08-24 — 单聊运行中插话:`_listen_for_control`(原 `_listen_for_stop`)加 `steer` + SteerChannel 接线

`_listen_for_stop` 改名 `_listen_for_control`,除 `stop`/`force_stop` 外新收 `{"action":"steer","input_content","client_msg_id"?}`:owner 运行中再发一句 → `SteerChannel.push(SteerInjection(source="owner_chat",...))` 折进**同一 run**,而非起新 run。空/非字符串 input 忽略(坏控制行不扰动 run);steer **绝不** cancel(那是 stop 的活)。
接线:`cancellation` 之后建 `steer_channel = SteerChannel(agent_id, channel_id="chat")` 并挂 `on_consumed`→给前端发 `{"type":"steer_consumed","ids":[...]}`;listener 收这个 channel + user_id;`BackgroundRun(..., steering=steer_channel)`([[background_run.py]]);run_id 出来后 `steer_channel.run_id = bg.run_id`(仅日志用)。三态气泡:push 后回 `{"type":"steer_queued","client_msg_id}`(→queued),消费后 `steer_consumed`(→merged)。
**单聊 vs bus 区别**:2 通目走**同一 WS**(前端往开着的 run WS 发),故**不需 RunRegistry/surface 路由**(WS 直接握 channel);**不落 steer_inbox**(ephemeral,in-flight;`run_id` 在 injection 上是 cosmetic,render/push 不读);run 结束前未消费 → 前端凭无 `steer_consumed` 兜底(重发为新 run)。**依赖 #354**(cloud `RemoteAgentLoopDriver` 传 steering)才在 prod 生效;dev/本地 in-process 即可。`owner_chat` 不装 `STEER_PROVENANCE_RULE`(owner 是可信权威,无冒充/提权面,与 team 不同)。

### 2026-08-24(审后补)— 上限 / 能力门 / reconnect

steer 分支抽成纯函数 `_route_steer(ws, channel, user_id, data, steerable)`(可单测),补齐审查三条:
* **上限(Critical)**:ephemeral 绕开 `steer_inbox` 的有界写边,故在 WS 侧**重加同两条上限**,常量**从 `steer_inbox_repository` import**(单一定义、防漂移):单条 `MAX_CONTENT_BYTES=128KB`、每 run 未消费 `MAX_UNCONSUMED_PER_RUN=500`;`client_msg_id` 限长 `_MAX_CLIENT_MSG_ID_LEN=128`(超长→自铸,不塞进 `STEER_ID_KEY`)。超限=**显式 `{"type":"steer_rejected","client_msg_id","reason"}` 帧**(`too_large`/`too_many_pending`),**push 之前**判、**绝不截断/静默丢**(否则乐观气泡永挂,比不做还糟)、拒绝路径**不发 `steer_queued``。
* **能力门(Important;审后订正)**:`_resolve_run_steerable(agent_id)` **问真 driver 的 `capabilities()`**(协商接缝),**不**硬编码 framework 名。关键:steerability 是 **driver** 的属性不是 framework 名的——`get_agent_loop_driver` 只要有 executor/broker URL 就一律返 `RemoteAgentLoopDriver`(cloud 上 `BROKER_URL` 恒设,默认值也生效),而它(未上 #354 时)**不声明 steering**;所以 cloud 上 nexus_power(默认 framework)跑的是 remote driver、`steerable` 若按名判会**错报 True**→气泡永挂。故按 `get_agent_loop_driver` 的 remote-vs-in-process 选择读所选 driver 的 `capabilities()`;remote 判据用**规范函数 `executor_seam_active()`**([[broker_client.py]],`AGENT_EXECUTOR_URL` 或 broker,`get_agent_loop_driver` 自己也是这个单一真值——不再手抄裸 env,其 docstring 明写「只查静态 var 曾让云端 guard 上成死码」)。`capabilities()` 与 URL 无关,故用占位 URL 选中 remote driver 类而不碰 broker(且 remote 臂 `RemoteAgentLoopDriver` 构造无副作用)。`run_started` 帧带 `steerable`(前端显/隐输入框),listener 对不可 steer 的 run 回 `steer_rejected reason=framework_no_steering`。best-effort,解析失败→False(安全)。回归 `test_steerability_asks_the_actual_driver_not_the_framework_name`(cloud+nexus_power 但 remote 无 steering→False;local→True)。**这条同时对 #354 前后成立**:上 #354 后 remote driver 对 nexus_power 声明 steering→cloud 也 True。
* **reconnect(Important)**:`run_reconnect` 帧带 `steerable=false`——重连的观察 WS **不持有** run 的 SteerChannel(它在原 WS handler 作用域),故前端隐藏输入,不发会永挂的 steer。重连侧回折 steer 是 follow-up(需 run 经 active_runs 暴露其 channel)。
* **持久化(Important,记为 follow-up)**:owner 这句 steer 走 ephemeral,**不落 chat memory / event_stream**。其**效果**经 assistant 回复被 `persist_turn` 保存;但**字面消息**刷新后不在历史、下轮 chat memory 也没有——消费时补写 chat memory(去重 optimistic 气泡)是**独立 follow-up**。已在 [[steer_schema.py]] docstring 标注「owner_chat 首落地不走 inbox」以免文档与代码分叉。

## 2026-08-14 — chat fast mode: 首包可带 fast_mode

`AgentRunRequest` 新增 `fast_mode: bool = False`（首包可选字段；缺省=
今日路径）。fresh-run 的 drive kwargs 构造抽成纯函数
`_fresh_run_drive_kwargs(request, *, session_id, working_source,
mcp_servers)`（沿 `_circuit_open_frame` 的纯函数可测 idiom），字段逐项
锁在 tests/backend/test_websocket_fast_mode.py——WS payload → drive 契约
不能静默丢字段。fast_mode 纯透传：策略在 AgentRuntime 的
`_resolve_turn_profile`，本文件零判断；reconnect 路径不涉及（profile
不持久化，重连只回放）。

## 2026-08-13 — WebSocket 账户状态闸门（停用用户不能起 run）

`/ws/agent/run` 在 cloud 分支验完 token、`token_user_id` 与 payload `user_id`
一致性校验通过、`websocket.accept()` 之后、进入 reconnect / fresh-run（"Starting
agent runtime"）**之前**，补一道**账户状态闸门**：
`if (await _account_state(token_user_id)) in NON_TRANSACTING_USER_STATUSES:` →
发一帧 `{type:"error", error_code: ACCOUNT_SUSPENDED}` 后
`websocket.close(code=WS_CLOSE_POLICY_VIOLATION)` 并 `return`。

**为什么必须在这里再来一遍**：账户闸门原本只在 HTTP auth middleware，而 middleware
**豁免 `/ws/*`**（WebSocket 在首帧自带 auth），于是一个被停用用户拿自己有效的 JWT
仍能经 WS **发起新 run**——而 WS 是产品**主**路径。复用 [[auth]] 的**同一个**
`_account_state` 读取器（TTL 缓存、fail-open、BINARY 大小写敏感）与**同一个**
共享集 `NON_TRANSACTING_USER_STATUSES`（[[entity_schema.py]]），两面绝不漂移。
`_account_state` 是 async，需 `await`。

**陷阱**：WS **不能**用 `auth_error_response()`——它返回 `JSONResponse`，无法经
WebSocket 发送；必须用本文件里其它 auth 失败already在用的「error-frame + close」
形式。`/ws/ping`（纯 liveness，另一个 route）**不 gate**。闸门在 cloud 分支内，
local 模式（单可信用户，无停用概念）天然不过它。

## 2026-08-11 — cloud 错误脱敏 + MCP egress SSRF 过滤（安全审计 P1-1/P0-3）

构造 `mcp_servers` 后调 `backend.routes._mcp_egress.filter_public_mcp_servers`（cloud only）丢弃解析到内网的 MCP，防运行时把内网响应带进模型上下文（复用文件头已导入的 `_is_cloud_mode`，不再另 import）。

顶层 `except` 原本把完整 `traceback.format_exc()` + `str(e)` 通过 `send_json` 发给前端，
泄露服务器路径、依赖版本、内部变量。现在按 `is_cloud_mode()` 分流：cloud 只发通用
`{"error_message": "Internal server error.", "error_type": "InternalError"}`（详情只进
服务端日志）；local/dev 仍发完整 traceback 便于调试。

## 2026-08-10 — accepted and started message stages

After validation a fresh turn records `message_accepted`; after Step 0 assigns
the durable run ID it records idempotent `run_started`. Their gap isolates
runtime-start failures from browser submission failures. The accepted fact is
keyed by the already-created WebSocket session ID and includes that ID as a
dimension, so reconnect/retry paths cannot inflate the stage count. Small
module-level helpers make both capture contracts directly testable.

## 2026-08-06 — AuthError 帧带 `error_code`

七个 AuthError 帧各自加一个 `error_code`（token_expired / token_invalid /
token_missing / identity_missing / identity_unresolved，取值见
[[auth_errors]]）。动机：这些帧原本只有 `error_type: 'AuthError'`，前端
[[wsAuthError.ts]] 无法区分"JWT 真的死了"和"local 模式 URL 的 x_user_id 跟
payload 对不上"（后者是前端状态 bug），一律当成会话死亡直接登出。

前端侧现在不管哪种帧都先走 [[sessionGuard.ts]] 探针确认，所以即使分类错了，
代价也从"整个会话"降到"这一次运行失败"。

## 2026-07-31 (三次) — 可见性判定改「agent 属主」（review R2 Critical #1）

`events.user_id` 是**触发方 key** 不是属主列：team run 写的是发送方
（`usr_<uid>`，agent→agent 中继则是 agent_id），channel run 在 owner
解析失败时退化为 agent_id。拿请求者与它相等比对会把所有可观察 team
run 判 Forbidden——恰好是 run 观察的旗舰面。改为：相等走快路径
（chat 型 run），否则 `AgentRepository.resolve_owner(agent_id)` 反查
属主比对；无法证明属主即不可见（fail-closed，覆盖上一轮的「无主行」
分支）。local（requesting 为空）行为不变。测试：
tests/backend/test_reconnect_visibility.py（5 条：usr_ 前缀/中继
agent/非属主拒/幽灵 agent 拒/chat 快路径）。

## 2026-07-31 (二次) — review 扫尾：归属收紧 + 状态检查降频

- 归属校验补「cloud 模式下 events.user_id 为空即拒」：event_stream
  现在装着全量 thinking/tool trace，无主行 fail-open 等于对全体已认证
  用户可见（旧形状本 PR 放大了影响面，review Minor #7）。
- tail-follow 的 events 终态/心跳检查降频到每 3 拍一次
  （_TAIL_FOLLOW_STATE_CHECK_TICKS）——它只负责结束 follow，迟几秒
  不可见，稳态查询量降约 1/3（每拍 2 次 → 1⅓ 次）。

## 2026-07-31 — 跨进程 run 的观察：replay-only 分支升级为 DB tail-follow

`_handle_reconnect` 遇到「running 但本进程无 BackgroundRun」（= run 活
在 trigger 容器 / 别的 backend 实例）不再 reconnect_warning+close，而是
`_follow_run_from_db`：记住回放末 seq，每 `_TAIL_FOLLOW_POLL_S`（1s）
增量拉 `event_stream WHERE seq > last` 下发（帧型仍是 `replay`，前端
无感知），终态发 `run_ended`，心跳停跳按既有规则报 run_ended(failed)
（只读，不改行 —— 周期清扫负责落账）。挂起的 `websocket.receive()`
兼作断线信号（观察路径客户端不发应用帧），asyncio.wait 的 timeout 就是
轮询节拍。低延迟需求出现时，这个循环内部就是换 Redis pub/sub 的 seam，
帧契约不动（铁律 #20）。这是 team roster / 未来 dashboard 观察 trigger
run 的读侧通道。测试：tests/backend/test_reconnect_tail_follow.py。

## 2026-07-15 — 用户 MCP 装配为 spec 形状（headers 随行）

DB 加载后组 `{name: {"url", "headers"?}}` 传 `pass_mcp_servers`（原
`pass_mcp_urls` 扁平 url dict）。日志只打服务名/数量，不打 headers 值。

## 2026-07-13 — Agent 实时层熔断器接入

fresh-run 路径加熔断器 `should_skip` 闸门：paused/cooling 时发一帧清晰的 `agent_circuit_open` error（severity fatal + cb_reason，**不静默**）并关 socket，不启动 run。帧文案由纯函数 `_circuit_open_frame(cb_reason)` 生成（reason→message 映射，可单测）。fail-open。


## 2026-06-11 — pass sender_user_id into trigger_extra_data

The chat WS now puts `sender_user_id = request.user_id` (the logged-in sender, JWT-validated) into trigger_extra_data, so the context builder can name the sender + derive is-owner (agent_runtime overrides ctx_data.user_id to the owner, dropping the original sender otherwise).

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
`ChatModule.persist_turn` 把 user 这条写进
`instance_json_format_memory_chat` 时，`meta_data.timestamp` 取的就是
`params.event.created_at.isoformat()`（assistant 那条才用 `utc_now()`，
即 run 结束时刻——两者刻意不同）。前端 ChatPanel 的 dedup 走 `role:content`
+ ±300_000ms 窗口（SAME_MESSAGE_WINDOW_MS），如果时间戳基准不一致，
即便差几秒也只是窗口在兜底——但用同一个字段作基准让"reconnect 注入
的 user bubble"和"run 结束后从 `/simple-chat-history` 拉回来的 user 行"
匹配精度最高（只差 DB INSERT 时 SQL `datetime('now')` 与 Python
`utc_now()` 之间的几个毫秒），不会出现双重 user 气泡。

> 2026-08-05 更正：这段原来写的是"持久化进 `agent_messages`"并钉了
> `chat_module.py:786`。`agent_messages` **没有写入者、0 行**，是墓碑表
> （见 [[agent_message_repository]] 2026-08-05）；聊天正文一直在
> `instance_json_format_memory_chat`。行号也已失效，故不再钉行号。

env_context 解析失败（JSON 损坏、列空）走 `suppress + warn-log`：
reconnect 仍然继续，只是前端那一帧拿不到 input_content，user 气泡
那一行就缺一下——比把整个 reconnect 路径搞挂好。

## 2026-05-13 — Phase D backend: force_stop 协议

`_listen_for_stop` 增加 `{"action":"force_stop"}` 分支——前端在用户点
graceful stop 后 10 秒没看到 cancelled 时弹"强制结束"，确认后发这条。
后端立即推 `{"type":"stopping","stage":"received","force":true}` ACK，
然后 cancel token；SIGKILL 实际由 Phase A C2 在 adapters.claude.sdk
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
6. state == running 但本进程 active_runs 没有（run 活在 trigger 容器 /
   另一台 backend instance）→ `_follow_run_from_db` tail-follow：
   增量推 `replay` 帧直到终态（2026-07-31 起；心跳停跳则
   run_ended(failed)）

**协议变化**

新增 ws inbound 字段：`run_id`（Optional，触发 reconnect 模式）
新增 ws outbound type：
- `run_started` —— fresh run 启动后立即推 run_id
- `run_reconnect` —— reconnect 模式入口
- `replay` —— history 回放
- `run_ended` —— terminal state + final_output
- 之前已有：`stopping`, `cancelled`, `error`, `heartbeat`, `complete`, ...
- （`reconnect_warning` 已删除，2026-07-31：跨进程分支改 tail-follow，
  不再有「拿不到 live stream」的道歉帧）

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
