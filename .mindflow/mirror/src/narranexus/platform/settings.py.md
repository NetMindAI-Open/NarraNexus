---
code_file: src/narranexus/platform/settings.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-09 — `claude_max_tool_use_concurrency`（默认 6，0 = CLI 默认；只对订阅 auth）

Claude Code agent loop 一条 assistant 消息里**所有并发安全工具调用**（并行 Read/Grep/MCP 与
sub-agent Task launch 都算）的并行度；CLI 自身默认 10。sub-agent launch 是其中一类，所以它也是
扇出的有界信号量：订阅账号每个 sub-agent 都是打同一配额的模型循环、CLI 对订阅 429 不重试，
无界扇出 = 429 风暴。注入点在 [[api_config]] 的 `to_cli_env`（settings → `CLAUDE_CODE_*` 正规
接缝），**只对 `SUBSCRIPTION_AUTH_TYPES`（oauth / oauth_token）注入**——keyed 账号 CLI 自己重试
429，注入只会让并行读文件变慢（首轮 review I1：默认值不能改全体用户线上并行度）。默认值 2026-09-10 从 4
抬到 6（二轮 review I3）：**背后没有测量**，只是「低于 CLI 的 10 / 比 4 少削并行读」的判断，旋钮保留。只限并发、
每个调用仍会跑，不是轮次/时间封顶（铁律 #14）。Env `CLAUDE_MAX_TOOL_USE_CONCURRENCY`，已进
`.env.example`；作用域同上面的 transient retry 旋钮（云端 executor 走代码默认值）。

## 2026-09-03（补注）— `is_cloud_mode` 不再读 `self.database_url`

转发到内核后只看进程环境；`Settings()` 从不带参构造（`settings.py` 末尾与 `backend/config.py`），
且 `.env` 已预注入 `os.environ`，所以实际无差。唯一消费者 `backend/routes/bundle.py:302`。

## 2026-09-03 — `Settings.is_cloud_mode()` 转发到内核解析器

原实现只看 `self.database_url`；现在与 auth/deployment_mode 同一答案（多认显式
`NARRANEXUS_DEPLOYMENT_MODE` 与 `DB_HOST`）。`.env` 已在本模块顶部预注入 `os.environ`，所以内核
读到的环境与 Settings 构造时一致（`test_settings_is_cloud_mode_follows_the_kernel`）。函数内
import 避免 settings（叶子模块）在 import 期拉入内核包。

## 2026-09-03 — `claude_transient_retry_attempts` / `claude_transient_retry_backoff_seconds`

Claude Code agent loop 在**订阅账号**（oauth / oauth_token）遇到 `rate_limit` /
`server_error` 时由适配器 resume 同一 CLI 会话重试（见 [[sdk]]）。CLI 对订阅账号的 429
不重试（判据 `status === 429 → !isSubscriber()`），`llm_max_retries` 注入的
`CLAUDE_CODE_MAX_RETRIES` 在那条路上无效，故单独一组旋钮：次数默认 3（0 关闭），退避
`"15,30,60"` 逗号秒数、不够用最后一个值。只限 RETRIES OF A FAILING CALL，不封顶轮长
（铁律 #14）。Env `CLAUDE_TRANSIENT_RETRY_ATTEMPTS` / `CLAUDE_TRANSIENT_RETRY_BACKOFF_SECONDS`，
已进 `.env.example`。**作用域**：env 覆盖只在 agent_loop 跑在 backend 进程里时生效（本地 / `bash run.sh` / DMG）；云端 agent_loop 在 per-user executor 容器里，其 env 是 deploy 仓 broker 的严格白名单（`broker/broker.py`，同 `NEXUS_POWER_POOL_SIZE` 的形状），未加 passthrough 前云端走代码默认值 3 次 15/30/60s。

## 2026-08-19 — 取消/恢复改为显式带 channel（本条不是 08-18 就定下的）

原来这里写着「卡订阅的取消/恢复由上游路由到它所属的那个账号」。那句话来自接入文档、
**从未实测**，而且与 [[netmind_billing_client]] 自己的注释（「不传 channel = 上游按
power 处理」）**直接矛盾** —— 评审读出来的。

两个端点现在显式带 `channel`：悲观假设下这是必需的（不带 = nexus 上新建的卡订阅
取消不掉，用户下个月照扣），乐观假设下是惰性字段。**单独开一节而不是改写 08-18 那段**，
因为这是评审推动的当日改动，混进旧小节会让人以为它是一开始就想清楚的。


## 2026-08-18 — `billing_channel`（默认 `"nexus"`）

选哪个 Stripe 账号收款。NarraNexus 本身就是接入文档里说的 "nexus 场景"，而且
**只有这个账号开了支付宝和微信**，所以它是默认值而不是可选项。上游把"不传"读作
原来的共享 "power" 账号；我们总是显式发出去，让 body 表达它真正的意思。

做成 setting 而不是常量只有一个理由：nexus 账号出事时，切回 `"power"` 一个 deploy
就能恢复原来的付款链路。**绝不能从客户端输入到达** —— 见 [[billing]]。

已知代价（写在这里，省得日后在客服工单里重新发现一遍）：之前走 "power" 付过款的
用户在这里是**另一个 Stripe customer**，第一次走 nexus 结账时不会出现已保存的卡。
免费额度和订阅状态两边落同一个 NetMind 账本；卡订阅的取消/恢复见下方 2026-08-19 条目。


## 2026-08-14 — `bus_max_workers`（默认 8，原为 trigger 里写死的 3）

message bus trigger 的并发 turn 上限。这是**我们自己的**资源决策，不是对 agent 运行时长
的限制（铁律 #14）：池子决定同时能服务几个房间，池子太小在用户那儿的表现就是「群聊死了」
——恰恰是平台有责任避免的那个失效模式。

写死成 3 让槽位短缺既**看不见**、又**必须改代码**才能修。8 是新的舒适下限：bus turn 几乎
全是 await（LLM + DB），槽位很便宜，而一个团队房内部接力就能同时占掉好几个。

槽位等待就在 `[bus-timing]` 行的 `queue_wait_s` 里面，所以调这个值再重跑
`make latency-report` 是**可测量**的改动，不是拍脑袋。

**跨仓依赖（再调之前必读）**：生产跑 `run_worker_supervisor`，poller + jobs + bus +
所有 channel trigger 共享一个 asyncio loop、因而共享**一个 MySQL 池**。那个池的大小定
在 deploy 仓 `stacks/narranexus-app/compose.yml` 的 `workers` 服务（`MYSQL_POOL_SIZE`），
其注释的算式是「poller(3) + jobs(5) + bus + 每个 channel worker/subscriber」——本项就是
算式里的 `bus` 项。在这边改而不回头看那个数，就是「bus 的改动变成 poller / jobs / 每个
IM channel 一起说『数据库变慢了』」的由来。

**为什么调高仍然便宜**：连接是**按查询**借还的，不是按 turn 占住。一个 bus turn 约 24
秒里有 20 秒在等 LLM，那期间不占连接——这也是池子一直按「典型并发查询数」而非「理论
任务数」来定的原因（光 channel trigger 每个就允许 50 个 worker）。

**再往上调需要证据**：`service_audit` 里出现 `worker_starvation` 行才说明池子真的是瓶颈
（见 `_check_worker_starvation`）；没有，就不是。

## 2026-08-04 — free-tier thinking 安全开关支持本地 `.env`

`FREE_TIER_AGENT_THINKING` 加入 `_DOTENV_PASSTHROUGH`，与同族的
agent/helper 模型变量对齐。云上 compose 直接注入容器 env，所以不依赖这张表；
这一项专门保证 `bash run.sh` / `make dev-backend` 从项目 `.env` 启动时，
`free_tier_default_thinking()` 承诺的显式 `auto` 逃生口真正能被读到。

## 2026-08-03 — `helper_prompt_probe_enabled`

新增一个默认 `False` 的开关，控制 [[_prompt_probe]] 是否打日志。

**为什么是配置项而不是常开**：它坐在每轮跑约 6 次的 helper 热路径上。留在这里而非
硬编码，是因为它属于「按实验开关」—— 要回答的问题（那约 6 次调用之间有没有 ≥4096
token 逐字节相同的前导，即 `claude-haiku-4-5` 的 `prompt_cache_min_tokens` 门槛）
一旦有了结论，开关就该关回去。

**为什么另一个开关不在这里**：配套的 `HELPER_PROMPT_DUMP_DIR` 只从环境变量读，没有
进 Settings。两者的**风险级别不同**：本项只输出长度与前导切片哈希（无内容），而 dump
会把对话原文落盘。让后者停在环境变量层，是刻意不给它一个「配置里勾一下就开」的入口。

启动器侧：`scripts/dev/dev-local.sh` 会把这两个变量转发进每个 tmux 窗口 —— 不转发的
窗口会安静地产出一次「没有任何测量」的运行，读起来像「helper 没发起调用」而不是
「探针没开」。

## 2026-07-29 (二次) — 删除 executor_resume_hmac_secret 与 agent_loop_resume_enabled(T6)

两个都成了死配置:

- `executor_resume_hmac_secret` —— 它保护的能力(跨 executor 边界携带 CLI 会话句柄)
  已不存在,见 [[executor_protocol]]。**云端部署要同步移除这个环境变量**。
- `agent_loop_resume_enabled` —— 它门控的 `_resolve_resume_session_id`
  (句柄式 resume 的决策入口)已随 T5 删除。现在的开关是
  `claude_synthetic_transcript_enabled`。

## 2026-07-29 — claude_synthetic_transcript_enabled

`claude_synthetic_transcript_enabled: bool = True`(env
`CLAUDE_SYNTHETIC_TRANSCRIPT_ENABLED`)—— 是否每轮自己写 CLI 的 resume transcript,
而不是依赖存下来的会话句柄。机制与理由见 [[transcript]] 与 [[sdk]] 同日条目。

关掉即回到句柄式 resume。**运维闸门,不是兼容层**:每一步都 fail-open(没有可续的
历史、或文件写不进去,turn 就照今天的样子跑、历史留在提示词里),所以这个开关只是
给"想整体停掉这个优化"留一个入口。

## 2026-07-29 — claude_cli_prefer_pinned / claude_cli_path

两个字段服务 [[cli_binary]] 的二进制选择:

- `claude_cli_prefer_pinned: bool = True`(env `CLAUDE_CLI_PREFER_PINNED`)——
  是否优先用 PATH 上经版本校验的 `claude`,而不是 SDK wheel 自带的那个。关掉
  就回到 2026-07-29 之前的行为(永远用捆绑的)。这是**运维闸门,不是兼容层**。
- `claude_cli_path: str = ""`(env `CLAUDE_CLI_PATH`)—— 显式路径,优先级高于
  pin 查找。**路径不存在时被忽略而非照传**:照传会变成每轮 `CLINotFoundError`,
  远比回落到捆绑二进制糟糕。

为什么需要它们:SDK `_find_cli()` 先查捆绑副本,所以"装了新 CLI"不等于"用上了
新 CLI"。而 SDK 0.1.43 捆绑的 2.1.56 不对 `tools` 数组做归一化,每轮换序、
打穿整个缓存前缀(实验 E3/E3c)。详细机制与 fail-open 规则见 [[cli_binary]]。

## 2026-07-28 — executor_resume_hmac_secret（HIGH review finding）

新增 `executor_resume_hmac_secret: str = ""`（env `EXECUTOR_RESUME_HMAC_SECRET`）。
用途见 [[executor_protocol.py]] 同日条目：给跨 orchestrator→executor 边界的
`resume_session_id` 签名，因为 `/agent-loop` **无鉴权是刻意设计**，而 resume 句柄
指向的 CLI transcript 落在**全租户共用**的 `CLAUDE_CONFIG_DIR`（就是本文件
`claude_cli_config_path` / `claude_oauth_config_path` 那两个目录，不按用户分），
配上可猜的 `working_path`，就是一条跨租户读别人对话的路。

**默认空 = resume 整体失效（冷启动），而不是"不校验"**——这是刻意的失败方向：
本地/桌面零配置照常工作（它们不跨 executor 边界），云端在密钥没发下来之前只是
少一个优化。**云端部署依赖（NarraNexus-deploy）**：同一个值必须同时注入
orchestrator env 与**每一个** executor 容器 env（容器不读平台 .env）。不配置不会
报错、不会失败，只会静默丢掉 resume——所以这条得写在部署清单里，而不是靠日志发现
（executor 侧确实会打一次性 WARNING，但那是容器日志）。

与 `agent_loop_resume_enabled` 正交：那是"要不要 resume"的运维闸门，这是"resume
跨网络时凭什么被采信"的凭据。

## 2026-07-28 — prompt_turn_context_relocation_enabled 开关(R4a,新 dev 结构重放)

新增 `prompt_turn_context_relocation_enabled: bool = True`(env
`PROMPT_TURN_CONTEXT_RELOCATION_ENABLED`)。turn-context relocation 的
kill-switch:开 = 每轮易变内容(temporal / narrative updated_at+current_summary /
recent_actions / 模块 contribute_turn_context)搬进当前轮 user message 的 [Turn context]
块,system prompt 轮间字节恒定可缓存;关 = context 装配与 R4 之前**逐字节一致**。
与 `agent_loop_resume_enabled` **相互独立**(relocation 惠及所有框架所有轮次,
resume 仅 claude_code;并成一个开关会让"排查 resume 关开关"连带回滚 prompt 结构、
再次全网打穿缓存)。四象限(开开/开关/关开/关关)都是合法状态。同 R2 哲学:
fail-open 运维闸门,非兼容 shim。消费方:[[context_runtime.py]]。
(本条为 R4 系列在新 dev 结构上的重放;原始实现日期 2026-07-25,原分支
feat/cli-session-capture,该历史不在本分支 mirror 中。)

## 2026-07-28 — agent_loop_resume_enabled 开关（resume 化 R2/R3）

新增 `agent_loop_resume_enabled: bool = True`（env `AGENT_LOOP_RESUME_ENABLED`）。
这是 agent-loop resume 的 kill-switch：关 = 完全回到今天的行为（每轮全量历史冷
启动）。**不是向后兼容 shim，是 fail-open 优化的运维闸门**——step_3 的 resume 决策
（[[step_3_agent_loop.py]]）在任何存疑情形都回落冷启动，开关只是把这条回落变成无
条件。默认 true、无新必填 env，部署无影响。

## 2026-07-24 — free-tier gateway passthrough + deploy env

`_DOTENV_PASSTHROUGH` += `SYSTEM_DEFAULT_LLM_GATEWAY_URL`,
`SYSTEM_DEFAULT_LLM_GATEWAY_ADMIN_KEY`, `SYSTEM_DEFAULT_LLM_GATEWAY_BACKEND_KEY`,
`SYSTEM_DEFAULT_LLM_GATEWAY_KEY_MAX_BUDGET_USD` — read via `os.environ.get()` by
[[system_service]] / [[gateway_key_service]] (cloud sets them as real container
env; listed so a local `.env` can configure them too). Deliberately NOT
passthrough: the on/off + model knobs (`SYSTEM_DEFAULT_LLM_ENABLED` / `_SOURCE` /
`_AGENT_MODEL` / `_HELPER_MODEL`) — the free tier is cloud-only and cloud
provides those as env.

**Prod upgrade checklist (deploy env this feature adds):** backend —
`SYSTEM_DEFAULT_LLM_GATEWAY_URL` / `_GATEWAY_ADMIN_KEY` / `_GATEWAY_BACKEND_KEY` /
`_GATEWAY_KEY_MAX_BUDGET_USD` (the per-run key ceiling — without it a leaked
ticket is uncapped, so **do not skip it**), and repoint
`SYSTEM_DEFAULT_LLM_ANTHROPIC_BASE_URL` / `_OPENAI_BASE_URL` at the gateway;
gateway container — `LITELLM_UPSTREAM_OPENAI_BASE` / `LITELLM_UPSTREAM_API_KEY` /
`LITELLM_DB_PASSWORD`. The upstream master key moved OUT of the old
`SYSTEM_DEFAULT_LLM_API_KEY` (removed from the backend) into the gateway.
Lockstep with the NarraNexus-deploy PR (铁律 #3).

## 2026-07-21 — helper-LLM one-shot 界值(Lark bug #2)

新增一组 4 个字段,专门约束 **helper_llm 一次性调用**(Instance Decision / job 分析 /
memory / social entity 等短、单轮、无工具的结构化提取)——它不是 agent_loop,故设界
不违反铁律 #14:

- `helper_cli_timeout_ms` (60000)、`helper_cli_max_retries` (1):CLI helper 子进程的
  每请求超时与重试。默认由 [[cli_helper]] 的 `_run_claude_oneshot` **覆盖**
  `to_cli_env` 注入的 agent-loop 值(~10min×10),否则坏/被劫持端点可挂近 100min。
- `helper_cli_total_timeout_seconds` (120):**单次 one-shot 的硬墙钟上界**
  (`asyncio.wait_for`)。三者刻意自洽:`60s×(1+1)=120s=total`,让配置的重试真能跑满
  而不是被墙钟提前砍掉(硬上界 vs 软预算的关系写在字段注释里)。
- `helper_json_repair_attempts` (3):Claude helper 结构化输出抠取/校验失败时的有界
  修复重试次数(见 [[anthropic_helper]] / [[cli_helper]])。

全部带默认值、无新必填 env,部署无影响。
## 2026-07-22 — skill_marketplace_local_registry 字段

新增 `skill_marketplace_local_registry: bool = False`(可从 .env 读)。为 true 时
本地/桌面实例自己当 skill/team registry(不 proxy 云端),用于 dev、离线演示、
cloud marketplace 上线前过渡。等价于 env `SKILL_MARKETPLACE_LOCAL_REGISTRY=1`,
但落 Settings 后 `make dev-backend` 无需前缀即可生效。


## 2026-07-09 — claude_oauth_config_path (OAuth config-dir isolation)

Added `claude_oauth_config_path` (default `~/.nexusagent/claude_oauth_config`),
a dir kept SEPARATE from both the host `~/.claude` and the keyed
`claude_cli_config_path` below. #72 (below) isolated only the keyed path and
left OAuth pointing at the real `~/.claude`, which re-exposed the same hijack
(personal `settings.json` `env` block overriding the OAuth run) AND raced the
user's own Claude Code on `~/.claude/.claude.json` (2026-07-09 incident). OAuth
now uses this isolated dir; `adapters.claude.sdk._stage_claude_oauth_credentials`
stages ONLY `.credentials.json` into it before the spawn (never `settings.json`).
Consumed by `api_config.ClaudeConfig.to_cli_env()`.

## 2026-07-08 — claude_cli_config_path (agent_loop config-dir isolation)

Added `claude_cli_config_path` (default `~/.nexusagent/claude_config`). It
becomes the `CLAUDE_CONFIG_DIR` of the keyed agent_loop CLI subprocess so the
host user's personal `~/.claude/settings.json` — whose `env` block outranks the
subprocess env we inject — can no longer hijack the provider (2026-07-08
incident: personal relay in that `env` block returned `503 No available
accounts` for every message). Consumed by `api_config.ClaudeConfig.to_cli_env()`.
Same user-home-absolute-path style as `base_working_path`. (2026-07-09: OAuth no
longer "keeps the real ~/.claude" — see `claude_oauth_config_path` above.)

## 2026-07-07 — deploy env vars added by the NetMind billing integration (count + prod/local impact)

The NetMind subscription/billing feature (PRs #62 + #70) added **5** deployment
env vars (names only — values are per-environment, see `.env.cloud.example`):

- `BILLING_API_BASE`
- `BILLING_API_TIMEOUT_SECONDS`
- `NETMIND_KEY_API_BASE`
- `NETMIND_INFERENCE_BASE`
- `NETMIND_USE_SUBSCRIPTION_ENABLED`

`NETMIND_AUTH_API_URL` is NOT one of them — it predates this (the NetMind login
feature). The system free-tier vars (`SYSTEM_DEFAULT_NETMIND_*`) are also separate.

**Local/desktop mode: unaffected — needs none of these.** The billing and
use-subscription routes are cloud-gated (`is_cloud_mode()` → 404 in local), so
these settings are never read off the local path. Local behavior is identical
whether or not they are set.

**Prod-upgrade checklist (so nothing is missed):**
- The three base URLs (`BILLING_API_BASE` / `NETMIND_KEY_API_BASE` /
  `NETMIND_INFERENCE_BASE`) DEFAULT to prod, so a prod deploy needs **no change**
  to them — the defaults are already the prod hosts.
- The ONE deliberate prod action is `NETMIND_USE_SUBSCRIPTION_ENABLED`: keep
  **False** until the C1 billing contract is verified end-to-end on prod, then flip.
- Dev/staging must override all three base URLs (+ the pre-existing
  `NETMIND_AUTH_API_URL`) to the dev NetMind env AND set the flag True. Full
  dev↔prod mapping lives in `.env.cloud.example`.

## 2026-07-07 — netmind_inference_base

Added `netmind_inference_base` (default prod `https://api.netmind.ai/inference-api`;
dev sets `NETMIND_INFERENCE_BASE=https://test.api.netmind.ai/inference-api`). Used
ONLY by the use-subscription minted-key path; must match the same NetMind env as
NETMIND_KEY_API_BASE / BILLING_API_BASE / NETMIND_AUTH_API_URL. Manual key paste
stays on prod. See [[providers]] / [[user_service]].



## 2026-07-06 — NetMind billing / subscription settings

Added the NetMind billing block (externalize-per-env, same pattern as
arena_api_base): `billing_api_base` (default prod `billing.api.netmind.ai`; dev
sets `BILLING_API_BASE=https://billing.api.protago-dev.com`),
`billing_api_timeout_seconds`, `netmind_key_api_base` (key-mint API, default
prod `inference.api.netmind.ai`; dev sets
`NETMIND_KEY_API_BASE=https://inference.api.protago-dev.com` — host migrated
2026-08-28 off the legacy Java gateway), and `netmind_use_subscription_enabled`
— the flag
gating the one-click "use my subscription" key-mint (default **False**, stays off
until the C1 billing contract is confirmed and a multi-worker distributed guard
lands; see [[providers]] / [[netmind_billing_client]] / [[netmind_key_client]]).

## 2026-06-18 — arena_api_base (per-env Arena)

Added `arena_api_base` (default `https://api.arena42.ai`). Externalizes which
Arena environment auto-provisioning registers against, so the dev stack can set
`ARENA_API_BASE=https://arena-dev-api.protago-dev.com` and keep dev test agents
off the prod ladder. Read in the backend process by `ArenaProvisioningService`
(not the executor — the agent's own calls use ARENA_API_URL baked into the
workspace skill at provision time). Same externalize-per-env approach as
APP_DOMAIN; no _DOTENV_PASSTHROUGH entry needed (backend reads it directly).

## 2026-06-11 — invite env passthrough removed

INTERNAL_INVITE_SECRET / INVITE_AUTO_ISSUE_CAP dropped from _DOTENV_PASSTHROUGH (feature retired).

## 2026-05-22 — LLM runtime resilience knobs (#7)

Added `.env`-tunable fields: `llm_api_timeout_ms` (→ CLI `API_TIMEOUT_MS`),
`llm_max_retries` (→ CLI `CLAUDE_CODE_MAX_RETRIES`), `llm_stall_probe_after_seconds`,
`llm_stall_probe_timeout_seconds`. Consumed by `api_config.to_cli_env()` (timeout
+ retries injected into the CLI subprocess) and `adapters.claude.sdk` (stall
health-probe cadence/timeout). Defaults chosen to bound a pathological hang
without cutting a legitimately long thinking pass (铁律 #14). Documented in
`.env.cloud.example`.

## 2026-05-18 — extend .env→os.environ passthrough whitelist

The bridge used to forward only the 4 LLM API keys from `.env` into
`os.environ`. Backend code that reads `os.environ.get()` directly (here:
`BUNDLE_FETCH_ALLOWED_HOSTS` in `backend/routes/bundle.py`'s
`/import/from-url` SSRF guard) was silently ignored — `bash run.sh` /
`make dev-backend` started without the value, the allowlist fell back to
`narra.nexus,www.narra.nexus`, and local dev couldn't fetch from
`localhost:3001`.

Added `_DOTENV_PASSTHROUGH` alongside `_API_KEY_FIELDS`. API keys keep
their "override shell env" semantic (operator wrote them in `.env` via
desktop app, must win); passthrough vars also forward (no separate
setdefault path — match the established pattern).

**When introducing a new backend config that's read via
`os.environ.get()` directly, add it to `_DOTENV_PASSTHROUGH`** —
otherwise `.env` silently has no effect and dev/ops will be confused.

## 2026-05-15 — extend dotenv→os.environ passthrough whitelist

The `.env → os.environ` bridge used to whitelist only the four LLM API keys.
Backend code that reads `os.environ.get()` directly (rather than through the
Settings object) — e.g. `backend/routes/invite.py` reading
`INTERNAL_INVITE_SECRET`, `backend/config.py` reading `INVITE_AUTO_ISSUE_CAP` —
got silently ignored: `.env` value never made it into `os.environ`, so
`bash run.sh` / `make dev-backend` would launch without seeing them.

Added `_DOTENV_PASSTHROUGH` alongside `_API_KEY_FIELDS`:

- API keys still get the original "override shell env" semantic (the
  desktop app writes them to `.env` and they must win)
- Passthrough vars also forward to `os.environ`, same write-unconditional
  behaviour (no separate setdefault path — match the established pattern)

Add new entries to `_DOTENV_PASSTHROUGH` whenever introducing a backend
config that's read via `os.environ.get()` and you want `.env` support.

# settings.py

Process-wide configuration object — reads `.env` and environment variables once at import time and exposes them as a typed singleton.

## Why it exists

Before this file, configuration was loaded through scattered `load_dotenv()` + `os.getenv()` calls across modules, making it impossible to see what was configurable from one place and causing subtle ordering issues (some modules loaded `.env` too late). `settings.py` centralizes every environment variable into a single `Settings` instance (built with `pydantic-settings`) that is created at module import time. Importing `from narranexus.platform.settings import settings` gives any module access to typed, validated configuration without touching `os.environ` directly.

## Upstream / Downstream

**Reads from:** the `.env` file at `_PROJECT_ROOT/.env` (three levels up from the file itself) and system environment variables. For API key fields, `.env` values are injected into `os.environ` before pydantic-settings reads them, overriding any pre-existing shell variables.

**Consumed by:** `database.py` (`load_db_config`, `_ensure_backend`), `db_factory.py` (`get_db_client`), `agent_framework/` (LLM API keys), `narrative/`, `module/`, and the FastAPI backend. Essentially every module that needs an API key, database URL, or path configuration imports `settings`.

**Also writes to `os.environ`** at the bottom of the file for `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, and `ANTHROPIC_BASE_URL`, so that third-party SDKs (like OpenAI Agents SDK) that read `os.environ` directly also see the correct values.

## Design decisions

**`.env` overrides shell env for API keys.** The standard `pydantic-settings` priority is "environment variable beats .env file." This is inverted for API key fields: the `.env` file is read raw with `_read_dotenv_raw()` and its values are injected into `os.environ` before pydantic-settings runs, so the user's explicitly configured keys always win over whatever was already in the shell. This matters for the Tauri desktop app, where the user sets keys through the UI and those values are written to `.env` — they must take precedence over any key that might be present in the launch environment.

**`model_validator` for path expansion.** `base_working_path`, `narrative_markdown_path`, and `trajectory_path` allow `~` in their values. The `_expand_user_paths` validator calls `Path.expanduser()` on them so callers never need to handle tilde expansion themselves.

**Empty-string cleanup for `ANTHROPIC_API_KEY`.** If `ANTHROPIC_API_KEY` is empty in `.env` (a blank line or explicit `ANTHROPIC_API_KEY=`), it is deleted from `os.environ` rather than set to `""`. An empty key makes the Claude CLI think an API key is configured and skips its OAuth fallback, breaking desktop authentication.

**`skip_module_decision_llm: bool = True`.** The LLM call that decides which module instances to activate was measured to take 2.5–3 seconds and always returned the same result. This flag lets the runtime skip it and load all capability modules directly. It is `True` by default.

## Gotchas

**`settings` is a module-level singleton created at import time.** If `DATABASE_URL` or an API key changes in the environment after the module is first imported (e.g., in a long-running process that reloads `.env`), `settings` does not update. Restart the process to pick up changes.

**`_PROJECT_ROOT` depends on the file's location.** The root is computed as `Path(__file__).resolve().parents[2]`. If the package is installed in a different directory structure (e.g., via a non-standard editable install), `_PROJECT_ROOT` may point to the wrong place and the `.env` file will not be found.

**`extra="ignore"` silently drops unknown variables.** Any environment variable that does not match a `Settings` field is silently ignored. If you mistype a variable name in `.env` (e.g., `ANTHROPIC_API_KEYS` instead of `ANTHROPIC_API_KEY`), pydantic-settings will not warn you.

**New-contributor trap.** The sync to `os.environ` at the bottom of the file only covers the four API key variables. Other settings (e.g., `DATABASE_URL`) are not written to `os.environ`. Code that tries to read `os.environ["DATABASE_URL"]` directly rather than `settings.database_url` will get nothing.

## 2026-08-14 — 「槽位很便宜」要加一个限定:便宜的是等待,不是持有

上面那句成立的前提是槽位周转快。2026-08-14 起团队房的回复**在 turn 内**代发,而槽位
到 **turn 结束**才归还 —— 按铁律 #14,agent 在回复之后继续干几十分钟是一等场景。于是
下一跳在上一个 turn 还在跑时就被派发,同一房间一次 D 跳接力峰值占用最多 D 个槽位
(旧顺序是 1 个)。

实践后果只有一条要记住:`service_audit` 里的 `worker_starvation` 在团队接力期是
**预期信号**,不是「有人的 agent 卡住了」,要连着当时有几个房间在接力一起读。
详见 [[message_bus_trigger]] 的 08-14 节。

(第 26-27 行讲的是 MySQL **连接**按查询借还 —— 那是另一件事,仍然成立。)

