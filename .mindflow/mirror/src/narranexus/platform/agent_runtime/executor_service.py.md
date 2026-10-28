---
code_file: src/narranexus/platform/agent_runtime/executor_service.py
stub: false
last_verified: 2026-10-28
---

## 2026-09-03（批 2a.5）— `/agent-loop` 透传 `deferred_tools` 给 driver（所有 driver 都收 `**kwargs`）

## 2026-08-24 — `/health` 报 `busy`：容器自己回答"我在不在干活"

broker 的空闲回收器**只看得见 turn START**（`ensure()` 每轮调一次，之后编排侧直接
对容器流式），所以它的 idle 计时器量的是"距这一轮开始多久"—— 一个跑得比 TTL 长的
turn 和一个被遗弃的容器长得**一模一样**。prod 的 `EXECUTOR_IDLE_TTL_SECONDS` 是
4h，而铁律 #14 说多小时的 turn 是一等场景，所以"把 TTL 调大"不是答案：没有既安全
又有用的 TTL。

**为什么由容器回答，而不是查编排侧 DB**：对"我能不能停这个容器"这个问题，容器自己
就是最准确的答案 —— 不依赖 events 行的写入窗口，也不依赖 run recording 有没有被
关掉。（stale **镜像**替换仍然用编排侧判决，因为那里的容器按定义跑着旧镜像、可能
根本没有这个字段，探它会永远答"说不准"，镜像就永远滚不动。）

**为什么记账必须是裸 ASGI 中间件，不能写在 handler 的 `finally` 里**：
`StreamingResponse.stream_response` 在碰 `body_iterator` **之前**就发
`http.response.start`。消费方此时已经走掉的话，那个生成器**从未被启动**，而关闭一个
从未启动的 async generator **不会执行它的任何代码**，`finally` 也包括在内 —— 计数
就再也降不回来，容器从此永远报 busy：不可回收、不会自愈、任何指标上都看不出来。
在中间件这一层，记账括住的是 `await self.app(...)`，正常结束/客户端断开/取消/流还
没开始就抛异常，所有出口都经过它。

用裸 ASGI 而不是 `@app.middleware("http")`：后者是 `BaseHTTPMiddleware`，会把流式
响应体经 anyio memory stream 重新包一层 —— 在唯一一条帧大到几百 KiB 的路径上多复制
一遍每个 NDJSON 帧。

**broker 的三条销毁路径里，两条现在听 `busy`**：空闲回收器和
`DELETE /executors/{user_id}`（后者的调用方是编排侧回收器，TTL 20 分钟，比 broker
自己的 4h 紧一个数量级，所以这个信号最先在那条路上被用到）。stale **镜像**替换仍用
编排侧判决 —— 那里的容器按定义跑旧镜像、可能没有这个字段。

**broker 探测走容器 IP 而不是容器名**（deploy 侧 `_container_ip`）：按名字探测会和
docker embedded DNS 共享失败源，而在途 turn 对那个 resolver 免疫（编排侧在**早已
建立**的连接上收帧）。第一版的逃生舱同时要求"按名字拨不通"，极性正好是反的 ——
broker 侧 resolver 一抖就两个条件同时满足、把健康的长 turn 杀了，而它本来要处理的
"服务器卡死"两个条件都不满足、从来没被处理。

**`/health` 必须是 `async def`**：FastAPI 会把**同步** handler 丢进工作线程，那样
读的线程和中间件写的线程就不是同一个 —— `min()` 是一次跨字节码的迭代，字典在迭代中
被 resize 会抛 `RuntimeError`；而 `busy` / `len` / `min` 三次独立读还可能落在一次
insert 的两侧，报出"busy 但没有年龄"这种自相矛盾的组合。放在事件循环线程上，这些
都不可能交错。原注释把这条的理由写成"单进程 asyncio 所以原子"，那个前提恰好被同步
handler 破坏了 —— 真正让它成立的是**读写同线程**。

**work 前缀按路径段匹配**，不是裸 `startswith`：后者会静默把将来的 `/watchdog`、
`/agent-loop-metrics` 算成 work，而一个高频的这种端点会把容器钉成永久 busy —— 症状
（永不回收）和根因（新端点名字撞前缀）之间毫无提示。

**`/health` 自己绝不算 work**（`_WORK_PATH_PREFIXES` 是前缀白名单，不是"任何请求"）
—— 否则 broker 的探测会自我实现，每个容器永远 busy。

**报最老那个在途请求的年龄**而不是只报个数：光有计数分不清"一个合法的 10 小时
turn"（必须持续报 busy）和"有东西被钉住了"。一个永远不结束的请求会让容器永久 busy，
而 broker 现在尊重 busy —— 年龄让这个状态变成看得见的事实。

**两处无界 await 一并封掉**（它们都能把 busy 钉死）：
- `/agent-loop` 的**请求体读取**加 `_BODY_READ_TIMEOUT_S`。这个端点无鉴权、且从
  **容器内部**可达 —— agent 自己的 Bash 就能 POST 一个 chunked 请求然后永不关闭。
  这是**解析预算，不是 turn 的上限**：body 收齐之后循环爱跑多久跑多久（铁律 #14）。
- office-watch 透传的 `sock_read` 加 `_WATCH_READ_TIMEOUT_S`。上游是
  127.0.0.1 的本地进程，长时间无数据是卡死而不是网络天气；而客户端半关闭（笔记本
  睡眠、NAT 丢映射：没有 FIN，也就永远等不到 `http.disconnect`）时，无界的
  `sock_read` 会把这个请求永久挂住。agent-loop 那条流**保持无界**（一个工具调用可以
  思考几小时不吐字节）。

## 2026-08-24 — 云端 live-steering:`/steer` ingress + `/capabilities` + `steer_consumed` 帧

executor 容器里 loop 是 in-process(`AGENT_EXECUTOR_URL` 未设→本地 `NexusAgent`,可 steer)。补上「一协议两传输」的云端一半:
* **`_InboundSteer`**:一 run 的 HTTP 版 steer channel。`POST /steer` 喂 `queue`(容器内 driver 照 stdin pump 一样 drain);`deliver_consumed(ids)` 不认识 producer(真 `SteerChannel` 在 orchestrator 进程),故把 ids 转发到 `consumed_out`,`_stream` 抽出来发成 `{"steer_consumed": …}` 帧。只有 `queue`/`deliver_consumed` 是 `NexusAgent` 鸭子消费的形。
* **`_STEER_RUNS: dict[run_id → _InboundSteer]`**:本进程在飞可控 run。可控 `/agent-loop` 开始时登记、流结束 `finally` 删。模块单例(非 seam):executor 一容器一用户、run 与其 `/steer` 都在本进程,无跨进程真值要搬(不同于 orchestrator 的 RunRegistry)。run_id 不可猜→无鉴权的 `/steer` 也挡得住乱注入。
* **`/agent-loop`**:读 `body["run_id"]`,有则建 `_InboundSteer` 登记、`steering=inbound` 传给 driver,`_stream` 把 `{"event": …}` 与 `{"steer_consumed": …}` 两种帧交织(消费帧至多晚一个 event,尾部再 drain 一次;**审后补:driver 抛异常时,`except` 分支也先 drain 一次 consumed 再发 error 帧**——orchestrator 命中 error 就停读,晚发的消费帧会丢,把 at-most-once 窗口缩成无谓重投);`finally` 摘登记。三种帧 `json.dumps` 均带 `default=str`(对称)。无 run_id=非可控,`steering=None`,原路径不变。
* **`POST /steer`**:`{run_id, steer}`→查 `_STEER_RUNS`→`queue.put_nowait`。**body 读取同 `/agent-loop` 加 `_BODY_READ_TIMEOUT_S` 解析预算**(审后补):同一无鉴权、容器内可达的端点,永不关闭的 chunked 请求会挂住 task+连接,`except TimeoutError`→408(体保持 `{"ok":False}` 与本端点 404/400 一致;`/agent-loop` 的 408 是它自己 `{"error":…}` 形状,不动)。未知 run_id→**404**(非静默 200):调用方必须知道没投递到,好让那行不被 ack、以新 turn 重现(铁律#16)。非 dict 的 steer→400。408/404/400 各自分支,pump 看来都是非 200→瞬态续 drain,但日志可区分。
* **`GET /capabilities?framework=`**:回 `get_agent_loop_driver(framework).capabilities()`(nexus_power={event_log,steering}),给 orchestrator 显式协商用;探测失败不 500。
消费回程与本地对称:runner 的 `steer_consumed` 行→容器内 `NexusAgent.deliver_consumed`→`consumed_out`→`_stream` 发帧→orchestrator 的 [[remote_driver.py]] `_handle_frame` 拦截→真 `SteerChannel.deliver_consumed`。

## 2026-08-20 — 启动 lifespan 预热 runner 池（EXECUTOR_PREWARM_FRAMEWORKS 门控）

`app` 挂 `_lifespan`：接受请求前对 `EXECUTOR_PREWARM_FRAMEWORKS`（默认 `nexus_power`）
里的每个 framework 调 `get_agent_loop_driver(name).warmup()` 预填 warm-runner 池，消除
进程首个 turn 的冷启动（dev 实测 ~12s→~3s）。claude_code/codex 天生 eager import，
无需预热。

门控让 ops 能在内存吃紧时设空关掉：每个容器一个 idle warm runner ~350MB，撞
`EXECUTOR_MEM_MB=1536` 硬上限与 admission `MIN_FREE_MEM_MB`（6144）准入水位；`broker`
原本只有 `NEXUS_POWER_POOL_SIZE` 这个全有全无开关，本 env 提供细粒度关停——**云端由
broker 透传**（deploy `broker/broker.py` 照 `NEXUS_POWER_POOL_SIZE` 先例 + compose
`EXECUTOR_PREWARM_FRAMEWORKS-nexus_power`，**单 dash** 保留 ops 显式设空的关停语义；
`is not None` 而非 truthiness 区分未设/关停），本地/dev 从进程 env 直接读。更彻底的
“按容器实际 framework 预热”（零浪费）需 broker 传每容器 user framework，记为 follow-up。warmup 失败
只 `logger.warning`、driver 无 `warmup`（如 `remote_driver`）打 `debug` 跳过——都不阻塞
启动。接缝由 `test_executor_service_warmup.py::test_real_nexus_power_driver_exposes_warmup`
（不 mock）钉住,防重命名/代理静默取消优化。详见 [[nexus_agent.py]]。

## 2026-08-19 — 执行器侧读取并转发 origin_declaration

`_stream` 调 `driver.agent_loop(...)` 时新增 `origin_declaration=body.get("origin_declaration") or ""`。键名与 `build_agent_loop_request` 写死一致（两处独立构造的隐患由测试断言防住）。

## 2026-08-10 (review 修正) — 字段改名 `extra_readable_roots` → `extra_accessible_roots`

纯改名，语义不变：这份授予同时管写与删（confinement 层检查 `file_path` 与 shell 路径），
旧名名不副实。详见 [[policy.py]]。

## 2026-08-07 — 从 body 取出 `extra_readable_roots` 交给 driver

与 [[remote_driver.py]] 对称的另一端。白名单 body 的两端必须成对改，否则字段在网络边界
被静默丢弃。

## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

/agent-loop 端点把 body["turn_profile"] 透传给容器内 driver。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`/agent-loop` 处理器把 body 的 `agent_id` / `expressive_tools` 转发给本地
driver(此前只转 4 个 kwargs,声明会在云端被丢弃)。

## 2026-07-29 — 不再授权 resume 句柄(T6)

删掉 `authorize_resume_session_id(body)` 这一步及传给 driver 的
`resume_session_id=` 参数。理由见 [[executor_protocol]] 同日条目。

值得记一笔的是**为什么 executor 侧不需要任何新增**:这里传的是
`messages=body["messages"]`,历史本来就在里面;容器内起的是**本地 driver**(即同一个
claude adapter),它自己 `split_for_argv` 拿到 history_entries、自己在容器文件系统里
写 transcript。原计划(T3)以为要新加 `transcript_turns` 协议字段,查证后不需要。

关键是这不是巧合:transcript 路径的 slug 与 CLI 的 `options.cwd` **派生自同一个变量**
(`self.working_path`),所以两者在任何环境下自动一致。

## 2026-07-28 — resume 句柄改为经 HMAC 校验后才采信（HIGH review finding）

`/agent-loop` 里 `resume_session_id` 不再直接透传，改为
`authorize_resume_session_id(body)`（见 [[executor_protocol.py]] 同日条目）。
原因：本端点**无鉴权是刻意设计**，但这只在"body 每个字段只描述本次请求"时成立；
resume 句柄指向的是**全租户共用** `CLAUDE_CONFIG_DIR` 里的 CLI transcript，配上
可猜的 `working_path`（`{base}/{user_id}/{agent_id}`），直连本端点即可读回别人的
对话。现在 orchestrator（真正做过 per-user 校验的一侧）签名，本端常量时间校验。

关键行为：**校验失败一律降级为冷启动，绝不 4xx**。resume 是优化，冷启动永远正确；
把签名/时钟/部署问题变成 turn 失败才是真的坏。端点整体仍然是无鉴权的内网信任面
——这里鉴权的是**一个能力**，不是整个请求。

**云端部署依赖**：`EXECUTOR_RESUME_HMAC_SECRET` 必须注入本容器 env（容器不读平台
.env，所以只能走容器 env），且与 orchestrator 同值；未配置 = 本容器忽略所有
resume 句柄并打一次性 WARNING。

## 2026-07-28 — 透传 body `resume_session_id` 给 driver（resume 化 R2）

`/agent-loop` 把 `body.get("resume_session_id") or None` 传进容器内
driver.agent_loop kwargs，与本地路径（[[step_3_agent_loop.py]] 经 TurnInput
直传）对齐——两条运行模式一起通（铁律 #7），代码不区分本地/云端。纯透传，
无逻辑；executor 容器内没有 DB，resume 失败后的句柄清除在 orchestrator 侧
step_4 做。

## 2026-07-24 — 透传 body `disallowed_tools` 给 driver（setup-residency B++）

executor 端把 `body.get("disallowed_tools") or None` 传进容器内
driver.agent_loop kwargs，与本地路径（[[step_3_agent_loop.py]] 直传）对齐——
remote 路径下未绑定 channel 的工具同样从模型上下文剔除。纯透传，无逻辑。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-10-28 — 文件日志迁出 workspace（安全事件后续）

`_resolve_executor_log_dir` 只认 `NX_EXECUTOR_LOG_DIR`（broker 把
executor_logs 卷的 per-user 子目录 bind 到容器 `/var/log/nx-executor`
并注入该 env）；未配置则返回 None，`main()` 只走 stdout（docker logs /
journald）。**刻意没有 workspace 回退**：旧行为把平台日志写进
`{user}/.executor_logs`，落在 agent 可读、files API / 目录树 / bundle
导出 / skills 打包都能看到的面上（2026-10-28 跨租户读事件的盘点项）。
`base_working_path` 现在是**收容护栏**而非回退来源：env 值 resolve 后
落在 workspace base 之内（含符号链接、点段）一律拒绝、fail-closed 到
stdout-only——配错 env 也写不回用户文件。`main()` 层与解析层各有测试
（无 env 时 `logger.add` 不被调用、workspace 下不出现 `.executor_logs`；
env 收容负例）。存量 `{user}/.executor_logs` 的清理归 deploy 侧发版脚本
（一次性逐用户 rm，见 deploy PR）。文件日志仍 best-effort。

## 2026-06-17 — 日志写到 user 目录（已于 2026-10-28 撤销）

`main()` 启动时把 loguru 文件 sink 落到**该用户 workspace 目录**下的
`.executor_logs/`(`_resolve_executor_log_dir`:容器只挂了一个 user 子目录,
取那个唯一子目录;取不到则回退 base)。这样每个用户的 executor 日志隔离、
随挂载卷持久化到宿主,便于按用户排查。stderr sink 保留(`docker logs` 仍可用)。
文件日志 best-effort(失败只 warning,不挂服务)。

## Why it exists

The agent-loop **Executor** — a thin FastAPI service that is the ONLY
tier which spawns the claude/codex CLI. Given an assembled prompt + the
resolved (scoped) provider configs + the workspace path, it runs the
LOCAL agent-loop driver and streams the raw event dicts back as NDJSON
(`POST /agent-loop`). This is the data-plane half of the
control-plane/data-plane split (binding rule #20).

## Security shape (the point of extracting it)

- **No platform master secrets.** Started WITHOUT the platform `.env`;
  the only credential it sees is the per-run scoped LLM key, arriving in
  the request body and applied to a ContextVar for the loop's duration.
  So `env` inside the agent shows nothing sensitive, and a compromise of
  this container yields ~nothing persistent.
- **No database.** All DB work (pipeline steps 0-2.5) happened in the
  orchestrator; the executor only runs the loop it's handed.
- **No self-recursion.** The executor container does NOT set
  `AGENT_EXECUTOR_URL`, so `get_agent_loop_driver` resolves to the LOCAL
  claude/codex driver here (the remote driver is only used by the
  orchestrator).

## Gotchas / future

- Streaming is NDJSON: `{"event": {...}}` per line, `{"error": {...}}` on
  failure. The remote driver re-raises on the error line to match
  local-driver exception semantics.
- Raw event dicts are JSON-encoded with `default=str` — if an event
  carries a type that doesn't round-trip cleanly, `ResponseProcessor`
  (orchestrator side) could see a degraded value; watch this when
  flipping the remote path on in prod.
- Per-agent/per-user workspace isolation is a DEPLOYMENT concern layered
  on top (per-user container mounting only `workspaces/{user_id}`) — not
  this module's job. This module just runs the loop it is given.

## 2026-07-13 — office live-preview watch endpoints

新增两个端点支持 office artifact 的实时预览(watch 必须跑在 executor 容器内,因为工作区 + agent 的 officecli 编辑都在这里):
- `POST /watch/ensure` {agent_id,user_id,file} → 在容器内 `ensure_watch`(detached spawn officecli watch),**返回容器为该文件分配到的端口** `{ok,port}`。容器自己拥有端口分配(每文件一个专属端口),后端不再猜端口(改自 2026-07-13:原来是后端 hash 出 port 传进来,多文档并发会串台)。由后端 `/office-watch/open` 云端分支调用,拿到 port 后铸 token。
- `GET /watch/{port}/{path}` → 反代到容器内 `127.0.0.1:{port}` 的 watch 服务(SSE 流式,X-Accel-Buffering: no)。由后端公共代理转发到这里。两者都无鉴权(内网信任,同 /agent-loop),但仍做端口 allowlist 防御纵深。
- `GET /watch/version?agent_id&user_id&file` → 容器内 stat office 文件返回 `{mtime,size}`,给前端 mtime 兜底轮询用(云端工作区在容器里,后端 stat 不到)。由后端 `/office-watch/version` 云端分支调用。

## 2026-09-07 — the executor boots the plugin platform

_lifespan calls boot_executor_plugins() first.


## 2026-09-07 — marks the plugin boot healthy only once it serves (round-2 🟡-6)

The plugin boot no longer declares itself healthy on its last line; this
entrypoint calls `plugins_boot.mark_host_healthy(<role>)` after the process is
actually serving, which is the only moment that may clear the boot-crash marker
and move the last-known-good registry snapshot. Reaching the boot and then dying
must count as a failed boot.
