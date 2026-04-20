---
code_file: src/xyz_agent_context/agent_framework/api_config.py
last_verified: 2026-04-20
stub: false
---

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
