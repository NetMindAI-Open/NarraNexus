---
code_file: src/xyz_agent_context/agent_runtime/executor_protocol.py
stub: false
last_verified: 2026-07-31
---

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

body 新增 `agent_id` / `expressive_tools`:投递面是 per-run 状态(同
disallowed_tools),必须显式过 `/agent-loop` 边界——否则云端 NexusPower
收不到声明,退回 mute。internal-trust 性质不变:两字段只描述本次请求。

## 2026-07-29 — 删除 resume 鉴权(T6)

删掉 `sign_resume_token` / `verify_resume_token` / `authorize_resume_session_id` /
`_resume_canonical_string` / `_RESUME_TOKEN_VERSION` / `_RESUME_TOKEN_TTL_SECONDS`,
以及 body 里的 `resume_session_id` / `resume_auth_issued_at` / `resume_auth_token`
三个字段(连带死掉的 `import time`)。

**为什么它当初必须存在**:`POST /agent-loop` 刻意无鉴权(internal-trust:executor
不持任何平台密钥、也不需要数据库),这在"每个字段只描述本次请求"时是可以接受的。
`resume_session_id` 破坏了这条性质——它指向**请求之外**的一个资源,而那个 CLI
transcript 住在**全租户共用**的 `CLAUDE_CONFIG_DIR` 里,配上可猜的 `working_path`
(`{base}/{user_id}/{agent_id}`),直接打这个端点就能让 CLI 重放并流回**别人的对话**。

**为什么现在可以删**:没有句柄再跨这条边界。claude adapter 在 executor **容器内**
自己写 transcript、turn 结束即删([[transcript]]),所以磁盘上没有可重放的东西、
也没有需要授权的能力。安全性不是被放弃,是**前提消失了**。

**云端部署影响**:`EXECUTOR_RESUME_HMAC_SECRET` 环境变量不再被读取,可以从部署配置
里移除。而删除 body 字段是**破坏性线协议变更**,要求 orchestrator 与 executor
**同批部署**(铁律 #2:不做兼容层)。

## 2026-07-28 — resume 能力的 HMAC 鉴权（HIGH review finding）

`/agent-loop` **无鉴权是刻意设计**（内网信任：容器无平台密钥、无 DB，校验全在
orchestrator 做）。这个前提成立的条件是 body 里每个字段只描述"本次请求本身"。
`resume_session_id` 打破了它：CLI transcript 落在**全租户共用的**
`CLAUDE_CONFIG_DIR`（`settings.claude_cli_config_path` /
`claude_oauth_config_path` 各一个目录，不按用户分），而 `working_path` 是可猜的
`{base}/{user_id}/{agent_id}`。于是直连该端点 + 知道受害者 working_path 和一个
有效 `cli_session_id`，就能让 CLI 加载**别人的对话**并 stream 回来。原来唯一的
缓解只是 session id 的熵。

本文件新增三件套（与 provider config 序列化同居，因为两端都要 import）：

- `sign_resume_token(...)` — orchestrator 侧铸 token。
  canonical string = `v1|resume_session_id|working_path|framework|issued_at`。
  每一段都承重：`working_path` 把句柄钉死在**一个** workspace（这正是越权成立的
  那个字段），`framework` 防止 claude_code 句柄被当 codex 重放，`issued_at` 限定
  重放窗口，`v1` 留将来换 canonical 布局的余地。分隔符用 `|` 是安全的——各段都
  不可能含 `|`（id 是 `prefix_hex`、framework 是注册表 key、path 是 POSIX 路径、
  issued_at 是整数），不存在歧义拼接。
- `verify_resume_token(...)` — `hmac.compare_digest` 常量时间比较（用 `==` 会通过
  耗时泄漏匹配前缀长度，足以逐字节爆破出摘要）。任何异常形态都返回 False，从不
  抛异常。
- `authorize_resume_session_id(body)` — executor 侧闸门，返回"这一跑真正允许用的
  句柄"。

**freshness 选了显式 `issued_at` 而不是 unix 分钟桶**：桶方案为避开边界竞争必须
试算多个候选桶（多次 HMAC，且实际 TTL 是模糊的）；`issued_at` 本身被 MAC 覆盖，
重放者改不动它，只是让校验方能算出年龄，于是窗口（`_RESUME_TOKEN_TTL_SECONDS`
= 300s）成了与桶粒度解耦的可调量。窗口**双向**判定（`|now - issued_at| <= TTL`），
免得一点时钟偏斜静默关掉 resume。

**默认空密钥 = resume 整体失效（冷启动）**：`settings.executor_resume_hmac_secret`
默认 `""`，此时 orchestrator 不带 token 字段、executor 直接忽略
`resume_session_id` 并打**一次性** WARNING（`_resume_secret_warning_emitted`，
不是 per-turn 日志——这是部署状态而不是请求事件）。本地/桌面因此零配置照常工作
（它们根本不跨 executor 边界）。**云端部署依赖**：NarraNexus-deploy 必须把同一个
`EXECUTOR_RESUME_HMAC_SECRET` 同时注入 orchestrator 与**每个** executor 容器，
否则云端只是静默失去 resume 优化（不会报错、不会失败）。

降级而非拒绝（`authorize_*` 返回 None 而不是 4xx）：resume 是优化，冷启动永远正确；
签名/时钟/部署问题不该升级成用户可见的 turn 失败。密钥与 token 都**从不入日志**。

上游 [[remote_driver.py]]（无需改动：它只 POST 这里 build 出来的 body），下游
[[executor_service.py]]。测试：tests/agent_runtime/test_resume_protocol_threading.py。

## 2026-07-28 — body 新字段 `resume_session_id`（resume 化 R2）

`build_agent_loop_request` 新增 `resume_session_id: Optional[str] = None`，
body 恒带该键（None = 冷启动）。与 disallowed_tools 完全同型的 per-run 状态。
旧 executor 容器不认该字段时安全降级：只是不 resume、照常冷启动，功能无损
（fail-open，无需 lockstep 部署）。上游 [[remote_driver.py]]，下游
[[executor_service.py]]。测试：tests/agent_runtime/test_resume_protocol_threading.py。

## 2026-07-24 — body 新字段 `disallowed_tools`（setup-residency B++）

`build_agent_loop_request` 新增 `disallowed_tools: Optional[list[str]]`，body
恒带 `"disallowed_tools": disallowed_tools or []`。旧 executor 容器不认该字段
时安全降级（只是不裁剪、多花 token，不影响功能）。上游
[[remote_agent_loop_driver.py]]，下游 [[executor_service.py]]。

## 2026-07-15 — 协议字段 `mcp_server_urls` → `mcp_servers`（spec 对象）

`build_agent_loop_request` 的 body 字段改为 `mcp_servers:
{name: {"url": str, "headers": {str:str}?}}`，用户 MCP 的鉴权头由此跨
orchestrator→executor 边界。**部署注意**：旧 executor 容器不认新字段（该次
run 的 MCP 集合为空），上线需 backend 与 executor 镜像同批重建并回收存量
nx-exec-* 容器。

## Why it exists

Wire format for the agent-loop Executor boundary. When step-3 (the only
claude/codex spawn site) is extracted into a separate Executor service,
the call that used to be in-process must cross the network. The hard part
is that the **scoped provider credentials normally travel via ContextVar**
(`api_config._claude_ctx/_codex_ctx`, set by the resolver in the
orchestrator) — a ContextVar does NOT survive a network hop. This module
serializes those configs so they cross explicitly.

## Key points

- `serialize_provider_configs()` — orchestrator side; snapshots the
  current task's resolved configs (via `api_config.snapshot_user_config`)
  to plain dicts. `None` entries preserved (reproduce exact ContextVar
  state, e.g. anthropic_helper unset).
- `apply_provider_configs()` — executor side; rebuilds the frozen
  dataclasses and calls `api_config.set_user_config`, so the SDK's
  `to_cli_env` resolves the same scoped key — **without the executor ever
  touching the DB or the resolver** (that's the whole point: executor
  holds no DB creds).
- `build_agent_loop_request()` — the `POST /agent-loop` body. Deliberately
  does NOT serialize `cancellation` (orchestrator cancels by aborting the
  HTTP stream; executor sees client disconnect).
- Lives in the core package (not `backend/`) so both the executor service
  entrypoint and the remote driver import it without a backend dependency.

## Gotcha

Provider config dataclasses are frozen — reconstructed via
`Cls(**dict)`. If a config gains a field, asdict↔kwargs round-trips
automatically; if it gains a non-trivial type, add explicit handling.

## 2026-07-07 — 快照/回放 cli_helper

`_CONFIG_TYPES` 加 `cli_helper: CliHelperConfig`，`apply_provider_configs` 回放时 `set_user_config` 传 `cli_helper`——远程 executor 才能复现订阅 helper 的 ContextVar 状态。
