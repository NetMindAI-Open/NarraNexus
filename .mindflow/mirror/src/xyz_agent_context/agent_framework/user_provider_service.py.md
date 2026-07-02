---
code_file: src/xyz_agent_context/agent_framework/user_provider_service.py
last_verified: 2026-06-10
stub: false
---
## 2026-06-10 (5th pass) — onboard live-verifies the key before writing

onboard_one_key now probes the key via provider_registry.test_provider
(GET /models on official endpoints — zero token; max_tokens=1 on
proxies; aggregators probed on their anthropic endpoint) BEFORE any DB
write. Policy: definitive auth rejection (401/403) → ValueError, nothing
persisted; transient failures (network/5xx/timeout) → proceed with
meta.key_check="unverified (<reason>)" so the UI warns — we never block
a good key because our egress hiccuped. Tests stub the probe (autouse
fixture in test_one_key_onboarding.py); a fake-key smoke against the
real Anthropic API confirmed 400 + zero rows.

## 2026-06-10 (4th pass) — helper_llm rejects OAuth providers

set_slot gained a defense-in-depth gate: helper_llm refuses providers
with auth_type=oauth (claude_oauth / codex_oauth) — CLI sign-in
credentials only drive the agent subprocess and can't make direct
Messages / Chat-Completions calls. Without the gate the misbinding
only surfaced at agent-loop time as NotImplementedError. Frontend
hides OAuth rows from the helper dropdown (ProviderSettings).

## 2026-06-10 (later) — onboard_one_key covers aggregator cards

provider_type now accepts netmind / yunwu / openrouter in addition to
anthropic / openai (aggregators are explicit-only — their keys have no
recognisable prefix). Aggregator cards create TWO linked provider rows;
the slot assignment routes by protocol (agent → anthropic row, helper →
openai row). Framework mapping: only pure-openai keys run codex_cli;
every aggregator serves claude_code through its anthropic endpoint.

## 2026-06-10 — onboard_one_key: the one-key orchestration primitive

New `onboard_one_key(user_id, api_key, provider_type=None)` wires a runnable
config from a single key: detect protocol (sk-ant- prefix → anthropic, else
openai; explicit provider_type overrides) → `set_user_agent_framework`
(claude_code/codex_cli — MUST precede the agent slot, set_slot validates
protocol against the framework) → `add_provider(card_type=protocol)` →
both slots on that same provider with model_catalog onboarding defaults.
Returns (config, new_ids, meta). The route layer (POST /api/providers/onboard)
stays thin: HTTP envelope + hot-reload + job rearm.

## 2026-06-10 — Framework-neutral reasoning params (feat/claude-sdk-adapter-upgrade)

SlotConfig gained two NEUTRAL knobs — `thinking: ""|on|off` and
`reasoning_effort: ""|low|medium|high|max` ("" = auto = adapter passes
nothing). They are deliberately NOT provider dialect (no "adaptive"/
"minimal"): NarraNexus will adapt more frameworks (Codex, pi, ...), so the
slot stores semantics and each agent-framework adapter owns the mapping +
clamping (rule #9). Persisted as `user_slots.params_json` (cloud) and via
the normal LLMConfig JSON dump (local llm_config.json) — both backends
expose them through the same set_slot(..., thinking=, reasoning_effort=)
signature with PUT semantics (omitted = reset to auto). Corrupt or
out-of-vocabulary stored params degrade to auto with a warning instead of
failing config load. Tests: tests/agent_framework/test_slot_reasoning_params.py.

# user_provider_service.py — 多租户场景的 per-user provider 数据库服务

## 2026-06-09 — funnel redesign: llm_slot_configured removed from set_slot

`set_slot` no longer emits any analytics. The `llm_slot_configured` event
and all of its supporting logic (`provider_method` derivation from source
field, agent-slot-only gate) were removed in the 2026-06-09 lean funnel
redesign that simplified the funnel to 5 events. The removal was deliberate —
not an accident or a forgotten call site. Future readers: if the chokepoint
instrumentation is ever needed again, `set_slot` is still the correct place
to add it (it is the single path `PUT /slots`, `POST /providers`
default_slots loop, and claude_oauth binding all share).

## 为什么存在

云端部署时，每个用户有自己的 API key 和模型偏好，不能共用单一的 `llm_config.json` 文件。这个服务把 provider 配置从文件系统迁移到数据库的 `user_providers` 和 `user_slots` 表，实现 per-user 隔离。接口设计刻意对齐 `provider_registry.py`，让调用方代码可以相对平滑地切换。

## 上下游关系

被 `api_config.py` 的 `get_user_llm_configs()` 和 `get_agent_owner_llm_configs()` 调用，在每次 agent turn 开始时加载 owner 的 LLM 配置。被 `backend/routes/` 中的 provider 管理 API 路由调用处理用户的 Settings 操作。

在做连接测试时，委托给 `provider_registry.provider_registry.test_provider()`，复用已有的测试逻辑，不重复实现。

`_is_cloud_mode()` 检查 `DATABASE_URL` 是否以 `sqlite` 开头来判断运行模式，但这个函数目前只是辅助判断，不决定哪些代码路径被使用——数据库存储始终被使用，区别在于是否回退到 `llm_config.json`（那个逻辑在 `api_config.py` 的 `_ConfigHolder` 里）。

## 设计决策

**和 `provider_registry.py` 的接口对称**：都有 `add_provider`、`remove_provider`、`set_slot`、`validate_slots`、`test_provider`。这让上层代码可以以相同方式操作两种存储后端，虽然目前没有统一抽象基类（将来可以提取）。

**Agent slot 协议由 `agent_framework` 决定**：`set_slot()` 不能只看静态 `SLOT_REQUIRED_PROTOCOLS`。当 `user_slots[agent].agent_framework ∈ {codex_cli, codex_cli_v2, codex_official}` 时，agent slot 接受 OpenAI-protocol provider；默认/Claude Code 路径仍要求 Anthropic。Codex OAuth provider 创建时也直接写入 `driver_type="codex_oauth"` 和 `auth_ref="codex-cli:~/.codex/auth.json"`，避免等待启动 backfill 才能被 resolver 使用。

**Framework 名字白名单（2026-06-08 evening 收敛）**：`_SUPPORTED_AGENT_FRAMEWORKS` 现在只接受两个 canonical 名字 `(claude_code, codex_cli)`。A/B 期间的 `codex_cli_v2` / `codex_official` 别名已 dropped——cutover 完了就拆，binding rule #2 (YOLO)。`set_slot` 的 source 白名单也对应回到 `agent_framework == "codex_cli"` 单条 equality 比较。

**三处必须同步**（加 v3 框架的时候）：
1. `agent_framework/__init__.py` 的 `register_agent_loop_driver` 调用
2. `provider_driver/resolver._KNOWN_AGENT_FRAMEWORKS` + `_is_codex_framework`
3. 本文件的 `_SUPPORTED_AGENT_FRAMEWORKS`

route 层 `backend/routes/providers.py` 现在 import 本文件的 `_SUPPORTED_AGENT_FRAMEWORKS`，不算独立条目。

**Codex provider 的 `models` 列表 = codex CLI 自己的 curated picker**：源头是模块顶层常量 `CODEX_CURATED_MODELS = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"]`——2026-06-02 直接看 codex CLI 交互式 "Select Model and Effort" 菜单确认。`gpt-5.5` 是 codex 自己标记 default flagship；`gpt-5.4` 是 strong everyday；`gpt-5.4-mini` 是 fast/cheap。Legacy 变种（`gpt-5-codex`、`gpt-5.2-codex`、`gpt-5.3-codex`）codex CLI 支持但不放 picker，要走 `codex -m <name>` 显式调用——不进 NarraNexus dropdown 默认列表。

**codex_oauth 的 `models` 列读时强制覆盖**：常量 `CODEX_CURATED_MODELS` 才是 source of truth；DB 里那列只是缓存。`get_user_config` 看到 `source=='codex_oauth'` 直接用常量替换 `models` 字段，**所以 code 改 seed 时下次 reload Settings 即时生效**——不需要 DB migration、不需要用户跑 SQL、不需要重建 provider。Codex 模型 user 不能自定义（user 自定义没意义，codex CLI 不认非 picker 的名字），这种"server 决定"的字段就该这么做。其他 source（claude_oauth、netmind、custom_openai 等）正常走 DB 存储 + user 自定义路径，不受这一规则影响。

**`CODEX_CURATED_MODELS` 同时也是前端 dropdown 的 source of truth**：当 agent slot + `agent_framework=codex_cli` 时，**无论 provider source 是 codex_oauth 还是 custom_openai**，前端 dropdown 都只能显示这三个模型。这一条由前端 `ProviderSettings.tsx::getModelsForSlot` 执行——它对 Custom OpenAI provider 的 `models` 字段（用户填了 gpt-4.1 / o3 之类）在 codex_cli 框架下直接忽略，返回硬编码的 curated 列表。Backend 这边只保证 codex_oauth 一定覆盖；如果只改 backend，Custom OpenAI 路径下用户能选到 codex CLI 不接受的 o3 / gpt-4.1，跑起来会被 codex 子进程拒绝。两层都要对齐。

**`codex_cli` 框架的 provider source 白名单 = {codex_oauth, user}**：set_slot 服务端校验 + 前端 `CODEX_ALLOWED_PROVIDER_SOURCES` 共同 enforce。**注意是 source 不是 protocol**——`"openai"` 是 protocol 值，所有 NetMind/Yunwu/OpenRouter 的 openai-protocol row 也会通过 protocol check，但它们 source 是 `netmind` / `yunwu` / `openrouter`，会被 source 白名单挡掉。`source = "user"` 是 "+ Custom OpenAI" / "+ Custom Anthropic" 表单加的所有 provider 的统一 source 标记（protocol 区分 anthropic vs openai）。第三方 OpenAI-protocol 聚合器**只暴露 chat-completions API，不实现 Responses API**——codex CLI exec 模式硬性要求 Responses API（reasoning model 全部只能这条路），跑起来会 missing model / tool-call 形状不对 / MCP 集成 broken。CC 框架就没这个问题：Claude SDK 接受 chat-completions endpoint，所以 CC + NetMind/DeepSeek 是 valid 组合，Codex + NetMind 不是。两个 framework 看似对称实则约束不同。

> **踩过的坑**：第一次写这个白名单时把 `"openai"` 当 source 用了，结果用户的 Custom OpenAI provider 因为真实 source 是 `"user"` 被错误过滤掉，dropdown 全空。**Provider 的 source 字段在创建分支里看清楚再写白名单**（见 [user_provider_service.py:265](src/xyz_agent_context/agent_framework/user_provider_service.py#L265)）。

**踩过的坑（写在这里防再犯）**：早期我们假设过 `gpt-5.4-codex` 存在（线性外推 "有 5.4-mini 就有 5.4-codex"）。**不存在**——OpenAI 5.4 系列只有 base/mini/nano，codex 路线 5.3 → 5.5 跳过了 5.4。之前 `codex exec --model gpt-5.4-codex` 返回 `"not supported when using Codex with a ChatGPT account"` 不代表模型存在，那是 OAuth gateway 对任意 codex 请求的统一拒绝字符串。

**API-key Codex 不需要专属 card_type**：早期我加过一个 `codex_api_key` card 类型，但功能上与"创建 Custom OpenAI provider + 把 slot 的 `agent_framework` 切到 `codex_cli`"完全等价——resolver 看 protocol=openai 就走 `_codex_config_from_card`，跟 source name 无关。OAuth 卡片有独立功能差异（auth.json 检测 + 凭据路径管理），API key 卡片没有。所以 API-key 路径走 `card_type="openai"` 即可，前端 "+ Custom OpenAI" 按钮就是入口。OAuth provider 仍保留独立 card_type 因为它有真实的 auth_ref 状态管理。

**models 字段以 JSON 字符串存储**：数据库里 `user_providers.models` 是 JSON 字符串（而非数组类型列），读取时用 `json.loads`，写入时用 `json.dumps`。这是为了保持对 SQLite 和 MySQL 的兼容性，避免数据库方言差异。

**linked_group 机制与 `provider_registry.py` 对应**：删除 provider 时先查 `linked_group`，找到同组所有 provider 一起删除，同时清掉对应的 slots。

**`_DUAL_PROVIDER_CONFIGS` 字典**：把 NetMind/Yunwu/OpenRouter 的双协议配置集中在一个字典里，比 `provider_registry.py` 的三个独立 builder 函数更紧凑，但内容是独立硬编码的，两处不共享。

## Gotcha / 边界情况

- 并发写同一用户的 provider 时存在 last-write-wins 竞态（upsert 操作），但云端场景每个用户通常只有一个活跃会话，风险低。
- `validate_slots()` 只检查三个 slot 是否存在，不校验 provider 的 API key 是否有效或 protocol 是否匹配 slot 要求（protocol 校验只在 `set_slot()` 里做）。

## 新人易踩的坑

- `user_providers.models` 和 `user_slots` 的 `updated_at` 用 ISO 8601 字符串存储（`datetime.now(timezone.utc).isoformat()`），而不是 datetime 对象。读回来需要 `datetime.fromisoformat()`。
- `get_user_config()` 不抛出异常，如果用户没有配置任何 provider，返回空的 `LLMConfig`，后续 `get_user_llm_configs()` 里才会因 slot 缺失抛出 `LLMConfigNotConfigured`。
