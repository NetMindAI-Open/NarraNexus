---
code_file: src/xyz_agent_context/agent_framework/api_config.py
last_verified: 2026-07-09
stub: false
---

## 2026-07-09 — _ConfigHolder.cli_helper property(代理无回退兜底)

`_ConfigHolder` 新增 `cli_helper` property(返回默认 `CliHelperConfig()`)。`cli_helper_config`
是 `_ConfigProxy`,ContextVar 为 None 时回退到 `getattr(_holder, "cli_helper")`——而 holder
原本没有这个属性(其余四个代理都有对应 property),一旦有人在 CLI-helper 路径外读
`cli_helper_config.framework` 就是 AttributeError。CLI helper 无全局/桌面来源(只由 OAuth
provider 派生到 per-task ContextVar),故这个 property 返回的是**永不作为真实配置**的默认值,
纯粹为让代理回退安全。

## 2026-07-09 — agent_id threaded through the resolver entry points

Per-agent overrides ([[resolver]]) reach the run + MCP-tool paths by threading an
optional ``agent_id`` through: ``get_agent_owner_runtime_llm_configs(agent_id)``
→ ``get_user_runtime_llm_configs(owner, agent_id=agent_id)`` →
``resolver.resolve(user_id, agent_id)`` / ``_get_user_runtime_llm_configs_strict``
→ ``resolve_user_runtime_llm_configs(..., agent_id=agent_id)``. Owner still bills;
the agent + helper slots resolve with this agent's overrides overlaid on the
owner default. ``get_user_llm_configs`` / ``_get_user_llm_configs_strict`` gained
the same optional param. The cloud SYSTEM free-tier branch ignores ``agent_id``
(fixed one-model pool). ``setup_mcp_llm_context`` is override-aware for free (it
funnels through the owner helper).

## 2026-07-09 — OAuth 分支也隔离 CONFIG_DIR(补完 #72 的漏)

事故延续:#72(下条)只隔离了 keyed 路径,OAuth 分支被特意放行、`CLAUDE_CONFIG_DIR`
仍指向真正的 `~/.claude`。结果同一个洞在 OAuth 上复现——`~/.claude/settings.json`
的 `env` 块(个人 relay)照样劫持 OAuth run,`503 No available accounts` 再现;
且 agent_loop 与用户自己的交互式 Claude Code **并发写同一个 `~/.claude/.claude.json`**,
互相清空(实测该文件在 55KB 与 50 字节间反复横跳,CLI 触发自救备份)。

修法:OAuth 也改指独立目录 `settings.claude_oauth_config_path`
(`~/.nexusagent/claude_oauth_config`,与 keyed 的 `claude_cli_config_path` **分开**)。
`to_cli_env()` 只负责设 `CLAUDE_CONFIG_DIR`(纯函数、无 I/O);真正把凭据搬进去的是
`xyz_claude_agent_sdk._stage_claude_oauth_credentials`——spawn 前**只拷 `.credentials.json`**
一个文件(绝不拷 `settings.json`),对齐 Codex 的 `_stage_codex_oauth_credentials`。

一个必须记住的边界——**newest-wins 拷贝**:仅当宿主 `~/.claude/.credentials.json`
比已暂存副本更新(或副本不存在)时才覆盖。这样既能让新的 `claude auth login` 传进来,
又不会把 CLI 在隔离目录里就地刷新过的 token 冲掉(宿主副本可能还带着已被消费/轮转
作废的旧 refresh token,盲目回灌会把用户登出)。宿主没有凭据文件时 warn + no-op,
不抛错。守卫测试见 `tests/agent_framework/test_claude_config_isolation.py`
(`test_oauth_isolates_config_dir` + 三个 `_stage_*` 用例)。

## 2026-07-08 — `to_cli_env()` 用 `CLAUDE_CONFIG_DIR` 隔离个人 `~/.claude`(治根)

> ⚠️ 下面这条描述的 OAuth「显式指向真正的 `~/.claude`」已被上面 2026-07-09 条**取代**——
> OAuth 现在也走独立目录。保留本条记录当时的推理链。

事故:某开发者机器上每条前端消息都 `503 No available accounts`。根因不在
netmind、也不在 NarraNexus 代码,而是 Claude Code 的 `~/.claude/settings.json`
里那个 `env` 块——它把 `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` 指到个人私
有 relay,**优先级高于我们注入给 subprocess 的环境变量**,把 agent_loop 的
provider 悄悄改道过去;relay 账号池耗尽 → 每次必挂。实测这个覆盖连 SDK 的
`--setting-sources ""` 都压不住。

修法:`to_cli_env()` 现在**总是**设 `CLAUDE_CONFIG_DIR`——keyed 认证
(api_key/bearer)指向独立的 `settings.claude_cli_config_path`
(`~/.nexusagent/claude_config`,CLI 会自动创建),那份个人 settings.json 从此
不被读取;`auth_type == "oauth"` 则显式指向真正的 `~/.claude`,因为 OAuth 的
凭据文件 `.credentials.json` 就在那、CLI 要从里面读 token。两个分支都显式赋值
(不留空、不省略),这样父进程若带了 `CLAUDE_CONFIG_DIR` 也无法经 SDK 的
`{**os.environ, **options.env}` 合并泄进来。守卫测试见
`tests/agent_framework/test_claude_config_isolation.py`;新增的 settings 字段
`claude_cli_config_path` 与 `base_working_path` 同风格(user-home 绝对路径)。

## 2026-07-07 — `get_user_runtime_llm_configs` 收敛到单一 ProviderResolver(#48)

根因:agent-run 路径(`get_agent_owner_runtime_llm_configs` → 此函数)原本自带
**第二份**决策树(读 `prefer_system_override` 后 if/else + `_use_system_default_strict`),
它**没有** `ProviderResolver.classify()` 的 #48 auto-switch。于是免费额度用尽 +
配了 own key 的用户,在任何**不经过 HTTP 中间件**的 run(后台 job/bus 触发)上仍被
硬 402,配置的 key 被忽略。

改法:`get_user_runtime_llm_configs` 删掉那份 if/else + `_use_system_default_strict`,
改为构造 `ProviderResolver` 并 `await resolver.resolve(user_id)`。这样 classify 的
auto-switch/通知在所有 run 路径统一生效,全项目只剩一棵决策树。

两个必须记住的边界:
1. **错误翻译**:resolve() 抛的是 `ProviderResolverError` 家族(≠ `LLMResolverError`)。
   agent_runtime / job_trigger / lark_trigger 只认 `LLMResolverError`(且按类名字符串
   匹配 `SystemDefaultUnavailable`)。所以此函数 catch 并翻译:
   `NoProviderConfiguredError→LLMConfigNotConfigured`,其余(`QuotaExceededError` 等)
   `→SystemDefaultUnavailable`。**改类名会连累那些字符串匹配**。
2. **SYSTEM_DISABLED 行为变化**:免费层被禁(本地/desktop 模式,或运营关掉)时
   resolve() 返回 None → 此函数 fall through 到 `_get_user_runtime_llm_configs_strict`
   (用 own config)。旧版对 opted-in 用户是硬抛 `SystemDefaultUnavailable`;现在与
   resolver 的 passthrough 语义一致(有 own→用之,无 own→`LLMConfigNotConfigured`)。

## 2026-07-03 — `to_cli_env` normalizes CLI aliases (upstream #57)

The four `ANTHROPIC_DEFAULT_*_MODEL` / `CLAUDE_CODE_SUBAGENT_MODEL` env
values now pass through `model_catalog.resolve_cli_alias` — the CLI's
internal calls (WebFetch summarizer, subagent dispatch) would otherwise
send a bare alias to a raw API transport and 400. OAuth path unchanged.

## 2026-06-17 — 新增 `snapshot_user_config()`(Executor seam 用)

`set_user_config` 旁新增 `snapshot_user_config()`,返回当前 task 四个
provider 配置 ContextVar 的值(claude/openai/codex/anthropic_helper)。
用途:orchestrator(有 DB + resolver)快照已解析的 scoped 配置 → 经
`executor_protocol` 序列化 → 发给 Executor 服务 → 它 `set_user_config` 重新
设上。让 scoped 凭据跨网络边界显式传递(本来只走 ContextVar,过网会丢)。

## 2026-06-17 — 删除 `_get_user_runtime_llm_configs_strict` 的 legacy fallback(铁律 #2/#5)

原函数是"先调单点 resolver,任何意外异常就 fall back 到一份 80 行手写的旧
解析逻辑"。那份 fallback 是 codex 出现**之前**写的——它无脑从 agent slot 拼
`ClaudeConfig`,完全不懂 codex,一旦被触发会把 codex agent 配成 ClaudeConfig。
按铁律 #2(年轻项目不留兼容 shim)+ #5(治根)删掉整段 fallback,函数现在纯
委托 `resolve_user_runtime_llm_configs`,意外异常直接冒出去报错。这是 agent-loop
活路径(`get_agent_owner_runtime_llm_configs` → 此函数)。resolver 侧把 codex /
helper 派发收进 driver 多态(见 resolver.py.md 2026-06-17)。

**同日 — `clear_user_config` 重置全 4 个 config ctxvar(LATENT-3)。** 原来只
reset `_claude_ctx`/`_openai_ctx`,留下 `_codex_ctx`/`_anthropic_helper_ctx` 携带
上一租户凭证。顺序多租户 worker(memory consolidation)靠 clear 防串户;把
provider_resolver 的 priming 修成 4 参后,若不同时补齐 clear,一个 resolve 被
跳过的 scope 会继承前一租户的 anthropic_helper → helper 工厂据此把 B 的 helper
路由到 A 的 Claude key。现 reset 全 4 个。

## 2026-06-10 — one-key onboarding: AnthropicHelperConfig joins the config stack

New `AnthropicHelperConfig` (api_key/base_url/model/auth_type) carries the
helper_llm config when that slot points at an anthropic-protocol provider —
the single-Claude-key path. It rides a new `_anthropic_helper_ctx` ContextVar
+ `anthropic_helper_config` proxy (holder keeps a benign empty default).
`set_user_config(claude, openai, codex=None, anthropic_helper=None)` — a call
WITHOUT the new arg resets the ctx to None, which is what makes
`get_helper_sdk()` dispatch safe across tasks. `RuntimeLLMConfigs` gains
`anthropic_helper: Optional[...] = None`. The legacy strict fallback's helper
block now branches on the provider protocol (anthropic → AnthropicHelperConfig,
`.openai` left empty). `setup_mcp_llm_context` upgraded from the 2-tuple path
to `get_agent_owner_runtime_llm_configs` so MCP tool processes see codex +
anthropic_helper too. `CodexConfig` gains neutral `thinking`/`reasoning_effort`
(mirror of ClaudeConfig's; dialect mapping in _codex_config_toml_builder).

## 2026-06-10 — merge `dev` into codex branch: embeddings out, Codex stays

Reconciling two opposite directions: `dev` retired embeddings (narrative/
memory routing is BM25 now), while the codex branch had made an embedding
slot *required*. Resolution = follow `dev`. `EmbeddingConfig`,
`_embedding_ctx`, the embedding field on `RuntimeLLMConfigs`, and the
embedding slot in the strict resolver are all gone. `RuntimeLLMConfigs` is
now `{claude, openai, codex}`; `set_user_config(claude, openai, codex=None)`;
`get_user_llm_configs` is back to a 2-tuple `(claude, openai)` — Codex rides
the `*_runtime_*` accessors. The guardrail test `test_embedding_removal.py`
was updated so the `set_user_config` signature assertion expects
`(claude, openai, codex)` (still rejects any embedding arg).

## 2026-06-10 — ClaudeConfig carries neutral reasoning params

`ClaudeConfig` gained `thinking` / `reasoning_effort` (both default ""
= auto), populated from the agent slot's SlotConfig at all three
construction sites (llm_config.json path, .env fallback — stays auto —
and the per-user resolver). The fields are framework-neutral; the
Claude-dialect mapping lives in xyz_claude_agent_sdk
(`_resolve_reasoning_options`), NOT here. `to_cli_env()` is untouched —
these ride ClaudeAgentOptions, not env vars.


## 2026-05-29 — add CodexConfig + codex_config ContextVar

Symmetric with the existing ClaudeConfig/OpenAIConfig/EmbeddingConfig
trio. New ``CodexConfig`` frozen dataclass carries ``api_key`` /
``base_url`` / ``model`` / ``auth_type`` for the Codex CLI subprocess
spawned by ``xyz_codex_cli_sdk.CodexSDK``. ``to_cli_env()`` mirrors
the ClaudeConfig invariant: explicit blank for ``CODEX_API_KEY``
when not in use so a parent-process env can't leak across tenants.

``base_url`` / ``model`` are NOT exported via env — Codex reads them
from per-run ``config.toml`` ``[model_providers.<name>]`` instead.
The wire is via ``_codex_config_toml_builder``.

Per-task ContextVar (``_codex_ctx``) + ``_ConfigHolder._codex`` slot
follow the existing pattern. Holder is initialised to an empty
``CodexConfig()`` by default — there is no .env/llm_config.json
source path because Codex auth flows through ``codex login`` (host
CLI) rather than NarraNexus config. Per-user overrides arrive via
the ContextVar at agent_loop time.

## 2026-05-31 — runtime config bundle includes CodexConfig

`RuntimeLLMConfigs` groups the four per-turn configs: Claude agent,
helper LLM, embedding, and Codex agent. `get_user_runtime_llm_configs()`
and `get_agent_owner_runtime_llm_configs()` return this bundle so
`AgentRuntime.run()` can inject `codex_config` before Step 3 selects
`CodexSDK`. The older `get_user_llm_configs()` still returns the three
non-Codex configs for call sites that do not drive the agent loop.

`CodexConfig` now carries `auth_ref` in addition to api key / base URL /
model. It is not exported as an env var; `xyz_codex_cli_sdk` uses it to
copy the host `codex login` auth file into the per-run `CODEX_HOME`.

## 2026-05-22 — to_cli_env injects API_TIMEOUT_MS + CLAUDE_CODE_MAX_RETRIES (#7)

`ClaudeConfig.to_cli_env()` now also sets `API_TIMEOUT_MS` (from
`settings.llm_api_timeout_ms`) and `CLAUDE_CODE_MAX_RETRIES` (from
`settings.llm_max_retries`). These are the Claude Code CLI's own knobs for a
per-REQUEST timeout and built-in transient-error retry. Previously unset →
inherited CLI defaults; now explicit + .env-tunable so a stalled request is
bounded and auto-retried (the "卡死无重试" fix). API_TIMEOUT_MS is per-request,
NOT a run total — it does not violate 铁律 #14 (no agent_loop cap); retry is on
the SAME provider so it does not govern model choice (铁律 #15).

## 2026-05-13 — `_get_user_llm_configs_strict` delegates to provider_driver

The user-provider branch now first calls
`provider_driver.resolve_user_llm_configs(user_id, db)`. That function
encapsulates the new single-point resolution path including reverse-
validation self-heal for broken slot.model bindings (the Xiong bug).
If the new resolver raises `LLMConfigNotConfigured` we re-raise to keep
the actionable message; any other exception logs a warning and falls
through to the legacy hand-rolled branch below — kept as a safety net
during the Phase 1 confidence window.

The legacy `_use_system_default_strict` path is untouched. The cloud
migration that turns env-var system credentials into a regular
`user_providers` row with `owner_user_id=NULL` (Phase 3) will collapse
that branch too; until then, opt-in `prefer_system_override=true` users
keep going through the old path.

See `reference/self_notebook/specs/2026-05-13-provider-unification-design.md`.

## 2026-04-20 change — strict 2-branch `get_user_llm_configs` (Bug 2)

The old 4-branch tree silently fell back to the system free tier whenever
`_get_user_llm_configs_strict` raised. That masked real configuration
errors and also depended on `QuotaService.default()` being bootstrapped
at process start — which `run_lark_trigger` had forgotten to do,
rendering the fallback permanently unreachable from the Lark process
(root cause of Bug 2 silent no-reply on Lark).

The new tree is driven solely by `user_quotas.prefer_system_override`:

  - `True`  → strict system free tier; raise `SystemDefaultUnavailable`
              (disabled by admin / quota exhausted). No silent fallback
              to the user's own provider.
  - `False` → strict user's own provider; raise
              `LLMConfigNotConfigured`. No silent fallback to the system
              free tier.

Error classes form a hierarchy:
  `RuntimeError` ← `LLMResolverError` ←
      `LLMConfigNotConfigured` / `SystemDefaultUnavailable`.

Consumers that want "any resolver failure" catch `LLMResolverError`;
consumers that want to branch UX per type catch the concrete subclass.
`AgentRuntime.run` catches the base class and yields a structured
`ErrorMessage(error_type=<subclass name>)`.

The new helper `_ensure_quota_service()` lazy-bootstraps
`QuotaService.default()` on first use via the shared `get_db_client()`.
Every entry point (backend.main, job_trigger, bus_trigger,
run_lark_trigger, standalone MCP runner) now works out-of-the-box
without each calling `bootstrap_quota_subsystem` itself — the trigger
that forgot is no longer a ticking bomb.

## 2026-04-16 addition — provider_source + current_user_id ContextVars

Two new auxiliary ContextVars were added alongside the existing
claude/openai/embedding ones, supporting the system-default free-tier
quota feature:

- `provider_source` ("user" | "system" | None) — set by ProviderResolver
  to signal which config branch produced the active user_config, so
  cost_tracker can decide whether to deduct the system quota after an
  LLM call.
- `current_user_id` — set by auth_middleware once the JWT is parsed, so
  cost_tracker can attribute usage without threading `user_id` through
  every layer of the LLM call stack.

Both default to None. Local mode / tests / any path that does not hit
auth_middleware simply sees None, making the quota hook a silent no-op.
Claim: these additions do NOT alter existing behaviour of `set_user_config`,
`_ConfigProxy`, or any proxy object — they are strictly additive.

# api_config.py — Centralized LLM config with per-task isolation

## 为什么存在

整个 agent_framework 层有四个不同的 LLM 消费方（ClaudeAgentSDK、OpenAIAgentsSDK、GeminiAPISDK、EmbeddingClient），每个都需要 API key、base_url 和 model name。如果各自读 `settings` 或 `os.environ`，在多租户并发场景下不同用户的 agent turn 会互相污染 API key（Alice 的 agent 用了 Bob 的 key）。这个文件提供一个统一的入口，用两级机制解决：全局 `_ConfigHolder`（延迟加载、可热重载）+ per-task `ContextVar`（asyncio task 级别隔离）。

## 上下游关系

所有使用 LLM 的组件都从这里读配置，而不直接读 `settings`：`xyz_claude_agent_sdk.py` 读 `claude_config`，`openai_agents_sdk.py` 读 `openai_config`，`embedding.py` 读 `embedding_config`，`gemini_api_sdk.py` 读 `gemini_config`。

上游写入者：`agent_runtime.py` 在每次 `run()` 入口调用 `get_agent_owner_llm_configs()` 然后 `set_user_config()`，把 owner 的三个 slot 配置注入当前 asyncio task 的 ContextVar。背后由 `user_provider_service.py` 从数据库的 `user_providers`/`user_slots` 表读取。本地单机模式的全局配置则来自 `provider_registry.py` 读取 `~/.nexusagent/llm_config.json`，fallback 到 `settings.py`。

## 设计决策

**ContextVar 而非全局变量**：`asyncio.Task` 创建时复制父 context，`asyncio.gather()` 内的每个 task 天然隔离。如果用全局 `_holder` 的 mutation，并发 trigger（`bus_trigger`、`job_trigger`）处理不同 owner 的 agent 时会 race condition。ContextVar 无需加锁，且在 task 结束后自动失效。

**`_ConfigProxy` 的类型欺骗**：`claude_config` 变量被标注为 `ClaudeConfig` 但实际是 `_ConfigProxy`。这是有意识的权衡——调用方代码写 `claude_config.model` 和以前完全一样，不需要改，但类型检查器会漏掉错误。代码内已有详细 TODO 说明正确解法（显式 `RuntimeContext` 参数传递，改动约 20 个文件）。

**LLM billing 归属于 agent owner 而非触发者**：`get_agent_owner_llm_configs()` 总是查 `agents.created_by` 作为计费主体，不用调用方传入的 `user_id`（后者可能是 Matrix sender、job target 等非 owner 身份）。

**Gemini 不走 ContextVar**：Gemini 仍从 `settings.py` 加载，尚未纳入三 slot 体系（代码注释有标注 "not part of the slot system yet"）。

## Gotcha / 边界情况

- `dimensions` 字段故意不传给 API：传了会在切换 embedding model 时造成 `SchemaNotReadyException`（不同模型原生维度不同，带 dimensions 参数调 API 会 400）。这个决策在注释里有解释，但容易被后续开发者"修复"回去。
- `auth_type="oauth"` 的 `ClaudeConfig` 的 `api_key` 是空字符串，`_holder.reload()` 里有 `json_claude if (json_claude.api_key or json_claude.auth_type == "oauth")` 的特判，新增判断逻辑时要同样处理 oauth 情况。
- `reload_llm_config()` 只重置全局 `_holder`，不影响已运行 task 的 ContextVar 值——hot-reload 对当前正在执行的 agent turn 无效，只对下一次 turn 生效。

## 新人易踩的坑

- 在没有调用 `set_user_config()` 的代码路径（如单元测试、独立脚本）里读 `claude_config.model` 会穿透 ContextVar 到全局 `_holder`，行为取决于环境配置。测试时最好 patch `api_config` 模块级别的代理对象或 patch `_holder`。
- 不要把 `embedding_config.dimensions` 传给 OpenAI embeddings API 调用，虽然 `EmbeddingConfig` 有这个字段但它只用于 UI 展示，真正的请求故意不带它。
- `LLMConfigNotConfigured` 是 `RuntimeError` 子类，在 `agent_runtime.py` 的 run() 里被捕获后会 yield `ErrorMessage` 给前端并 return，不会继续执行后续步骤。

## 2026-07-07 — CliHelperConfig（订阅 Helper 第三通道）

新增 `CliHelperConfig`（framework=claude_code|codex_cli + model/base_url/auth_type/api_key）、`_cli_helper_ctx`、`cli_helper_config` 代理、`RuntimeLLMConfigs.cli_helper`；`set_user_config` 加 `cli_helper` 形参，`snapshot_user_config`/`clear_user_config` 同步。用于让订阅（OAuth）登录同时覆盖 helper 槽——helper 走 CLI 一次性（见 cli_helper_sdk.py）。dispatch 优先级 cli>anthropic>openai。

## 2026-07-07 (实测跟进) — to_cli_env 两个致命 env 修复

本地实测 claude_oauth 后发现两个让 `claude` 子进程直接退出码 1 的 env 问题(主循环和 CLI helper 同死):
1. **CLAUDECODE 嵌套守卫**:后端若从 Claude Code 会话里启动(dev 常见),继承的 `CLAUDECODE` 让每次 spawn 报 'cannot be launched inside another Claude Code session'。 to_cli_env 现在显式置空(平台受管子进程,非人为嵌套)。
2. **别名自指**:oauth 路径 `resolve_cli_alias` 保留家族别名('opus'),但把 `ANTHROPIC_DEFAULT_*_MODEL` 指向别名是自引用 → CLI 报 'issue with the selected model' 拒启。现在别名时重定向置空(官方后端无漂移风险),仅保留接受别名的 `CLAUDE_CODE_SUBAGENT_MODEL`;具体 id(api_key 转换后)行为不变。
