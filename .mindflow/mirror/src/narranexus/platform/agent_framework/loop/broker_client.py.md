---
code_file: src/narranexus/platform/agent_framework/loop/broker_client.py
stub: false
last_verified: 2026-08-24
---

## 2026-08-24 — `stop_executor` 返回"到底停了没有"

broker 现在会在**容器自报有在途工作**时拒绝停机，返回 200 + `status: "busy"`。
它看得见这一侧看不见的持有者 —— office-watch 会话跑在容器**内部**、不留 run 行，
所以编排侧的 oracle（events 表）会把这个用户读成空闲。

因此拒绝是**正常结果而非错误**，调用方必须能和成功区分开：当成停了的话，
[[executor_reaper.py]] 会把该用户的 idle 戳丢掉，那个从没被停掉的容器就再也不会被
重新考虑（"claim 了但没动手"那类泄漏，见 #340）。返回 `bool` 而不是抛异常，是因为
它本来就不是错误路径。

没配 broker 时返回 `True`：没有要停的东西，也没有要让调用方继续持有的东西。

## 2026-08-21 — `ensure_executor` 带上 `allow_stale_replace`

同一根因（2026-07-31 prod 误杀在途 run）的**第二个杀手**：broker 的 stale 镜像
替换。本模块只负责**运**这个判决，不产生它 —— 它是传输客户端，"这个用户此刻忙
不忙"是编排侧 DB 里的事实，判决由掌握上下文的那一层给：step 3 见
[[step_3_agent_loop.py]] 的 `_ensure_executor_for_run`（排除提问者自己），
**prewarm 也传** —— 它不是 run，而且它整个存在的理由就是把冷启动搬到用户等待之外，
留默认 `False` 会让镜像替换被推迟到用户的这一轮里全额付掉。**注意不是"容器上没人"**：
判决只覆盖 recorded run，容器里可能有活着的 office-watch 会话，见
[[narramessenger.py]] 2026-08-21 条目里的残留风险。

真正必须保持默认 `False` 的是 office_watch 代理（[[proxy.py]]）：那是活着的会话，
但**不是 recorded run**，判决函数看不见它 —— 传 `True` 等于授权 broker 把用户正在
用的容器拆掉。判决函数因此叫 `no_live_recorded_run_for`（说证据）而不是
"…is_safe"（说结论）。

**默认 `False` 是两个方向上都安全的那一侧**：它只可能**推迟**镜像滚动，绝不会
杀 run。所以漏传只会降级不会出事，两仓部署顺序反了也不掉 run（新 broker + 老
编排 = 镜像暂时不滚；老 broker + 新编排 = 多一个它不认识的字段，pydantic 忽略）。

**命名只 gate 这一个理由**：broker 还有别的替换理由（容器拨不通、容量周转），
那些必须保持无条件 —— 拨不通的容器上没有可被打断的 run，拒绝替换只会把用户钉死
在一个死容器上。

响应里的 `stale_replace_deferred` **必须响**（`logger.warning`）：推迟是对的，但
不能是静默的 —— 用户会在旧 executor 代码上多跑至少一轮，而 wire-protocol 变更后
的旧 executor 是**不报错地**降级（2026-07 mcp_servers 改名让旧 executor 拿到空
MCP 集）。它在下一次"没有在途 run"的 ensure 上自愈。

## 2026-08-11 — `executor_healthy` 转公开（去重健康探测）

`_executor_healthy` 升为公开 `executor_healthy(executor_url, *,
timeout=5.0)`：吃 executor **base URL**、自己拼 `/health`，永不 raise。
动机：prewarm 路由（`backend/routes/channels/narramessenger.py`）此前
私有不可导而复刻了一份同逻辑——现在全仓只有这一个探针。`timeout` 参数
化是给热路径用的（prewarm 双端点传 1.0，铃响中不能被卡死容器拖满默认
5s）；`wait_until_ready` 内部改调它（默认 timeout），测试的 monkeypatch
seam 名字随之是 `executor_healthy`。

## 2026-08-10 — ensure 响应透传 identity_token（MCP caller auth）

`ExecutorEnsureResult` 新增 `identity_token`(默认 None):broker 用私钥签的
per-user Ed25519 token,**每次 ensure 都新鲜**(暖复用也返新 token,这正是选
「ensure 响应」而不是「executor env 注入」的原因——容器跨 run 长活,env 只在
创建时注入,短 TTL 会过期)。旧 broker 响应缺字段 → None,dispatch 侧不 stamp,
无 lockstep 硬依赖。消费方:[[step_3_agent_loop.py]] `_dispatch_identity_token`。

## 2026-07-31 — executor_seam_active():「本进程不跑 CLI」的单一判据

新公开函数:BROKER_URL(dev/prod compose 实际设的)**或**静态
AGENT_EXECUTOR_URL 任一存在即 True。给 verify_live 的控制面守卫用——
凡是拿本地状态(PATH、~/.codex、~/.claude)下 CLI/凭证判决的代码,
seam 生效时必须判"本节点无法决定"。教训:PR #224 第一版守卫只认
AGENT_EXECUTOR_URL,而部署仓从不设它,守卫在云上是死代码。

## 2026-07-22 — broker/冷启动不可达 → 类型化 ExecutorUnreachableError

两处改为抛类型化异常（[[executor_errors.py]]）：
- `ensure_executor`：httpx 传输错误（`httpx.TransportError`）→
  `ExecutorUnreachableError`。broker 的 HTTP status 错误**不**转换（照旧上抛）。
- `wait_until_ready`：容器超时未就绪，`RuntimeError` → `ExecutorUnreachableError`。

目的：冷启动阶段的不可达也能被上层 [[step_3_agent_loop.py]] 按类名 surface 成
`infra_transient` 可读错误（配合 step_3 把 try 边界上移到 ensure/warm 之外）。

## 2026-06-18 — wait for cold-started executors before driving

`ensure_executor` returns as soon as the broker `docker run`s the container —
it does NOT wait for uvicorn on :8020. So a cold start (`cold_started=True`)
returns a not-yet-ready URL; connecting immediately races the boot and the run
wrongly drops into the fallback path. New `wait_until_ready(executor_url)`
polls the executor's `/health` (via `executor_healthy` — public since
2026-08-11, still the monkeypatch seam for tests) until 200 — condition-based, not a fixed sleep, and NOT an agent-loop
cap (rule #14); it only waits for infra. step_3 calls it on cold start, right
after emitting the `executor.warming` UX event and before driving the loop.
Raises if the container never comes up within the timeout (genuinely broken).

## 为什么存在

orchestrator 侧调用 Executor Broker(部署在 deploy 仓库 `broker/`)的薄客户端。
云端每个用户的 agent-loop 跑在 broker 起的 **per-user Executor 容器**里(只挂该
用户 workspace、无平台密钥)。executor URL 因此**按用户动态**——本模块通过让
broker"确保该用户 executor 在跑"来现取它的 URL。

## 关键点 / 坑

- **API**:`ensure_executor(user_id) -> ExecutorEnsureResult | None`,带 `url` +
  `cold_started`(broker 返回 status=="started" 即冷启动)。`cold_started` 驱动
  前端"唤醒"UX(见下)。
- **`BROKER_URL` 门控**:只有云端 orchestrator 设它。未设(本地/桌面,或旧的
  单 executor 静态 `AGENT_EXECUTOR_URL` 模型)→ `ensure_executor` 返回
  `None`,调用方(step_3 → `get_agent_loop_driver`)回退。所以这是**附加且向后
  兼容**的。
- **唤醒 UX**:`cold_started` 时 step_3 发 `ProgressMessage(step="executor.warming",
  running)`,醒来第一个事件前发配对 `completed`;前端 `WakingOverlay` 据此虚化
  聊天面。见 `[[../../../../frontend/src/components/chat/WakingOverlay.tsx]]`。
- **冷启动触发点**:`broker.ensure` 可能拉起一个容器(数秒),故 timeout 放宽
  (120s),且 run 启动流程要向前端发"正在唤醒"状态(见 handoff 文档的唤醒 UX)。
- **失败要响**:broker/传输出错时**抛**异常,不静默回退到进程内 spawn——那会
  破坏隔离。云端宁可这一次 run 失败并暴露错误。
- 调用链:`step_3 → ensure_executor(user_id) → get_agent_loop_driver(executor_url=...)
  → RemoteAgentLoopDriver(该用户容器)`。executor 看到的 workspace 路径与
  orchestrator 一致(两边 BASE_WORKING_PATH 都是 `/opt/narranexus/workspaces`,
  nested 布局 `{user}/{agent}`),故 `working_path` 直接透传,无需翻译。
- `stop_executor(user_id)`:DELETE /executors/{user},供 idle-cull 用(见
  [[executor_reaper.py]])。同样 `BROKER_URL` 门控(未配置则 no-op);出错抛给
  reaper,reaper 记录并跳过,broker label-based reaper 兜底。
