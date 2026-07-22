---
code_file: src/xyz_agent_context/agent_framework/user_provider_service.py
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 — set_slot 新增 `actor_is_staff` 参数（云端 netmind-only 下沉）

`set_slot(..., *, actor_is_staff: Optional[bool])`——**keyword-only 必填，
刻意无默认值**：静默 bypass 正是 manyfold 缺口的成因，新调用方漏传参数会
直接 `TypeError` 而不是悄悄绕过策略。prov 行加载后调 [[cloud_policy]] 的
`ensure_slot_provider_allowed`，违规抛 `CloudPolicyViolation`（路由映射
403）。`None`（**调用点必须显式写出**）= 受信内部调用方（onboard 的槽位
绑定、OAuth 卡自动补槽、provisioner）不检查——它们的策略决定在上游：云端
非 staff 的 onboard 根本走不到 set_slot（activate=False），OAuth 添加在
路由层就已 staff-only。此前该检查在路由层做，多一次 prov 查询且与
per-agent 写入器不共真源（review 修复；必填化为同日二次 review 加固）。

## 2026-07-17 — onboard meta 增加 `activated` 布尔

`onboard_one_key` 的返回 meta 多一个 `"activated": activate`：False =
register-only（key 已存、framework/槽位未动），UI 据此**不得**宣称"你已运行在
X 模型上"（OneKeyOnboard 的成功面板改显示 "Key saved" + 指向本地版）。动因：
云端 netmind-only 策略下 /onboard 路由对非 staff 传 activate=False（见
[[providers]] 2026-07-17），旧 meta 仍带 agent_model 等字段会误导前端。
needs_replace 早退分支不带它（UI 先分支 needs_replace）。

## 2026-07-10 — `onboard_one_key` 新增 `activate` 参数（register/activate 分离）

`onboard_one_key(..., activate: bool = True)`。`activate=True`（默认，保持旧行为）
照旧：设 framework + 绑 agent/helper 槽。`activate=False` 时**只** `add_provider`
建 provider 行，**不动** framework、**不绑槽**——用于登录时自动 register 一张
NetMind 卡但不抢占用户已有配置（见 [[netmind_provisioner]] 的 register/activate
拆分）。`add_provider` 无条件执行，返回 `(config, new_ids, meta)` 不变。

## 2026-07-10 — 移除 codex agent slot 的 source 白名单(恢复 pre-#81,铁律 #15)

`validate_slot_binding` 的 **codex source 白名单已删除**。以前 codex_cli 的 agent
slot 被 `source ∈ {codex_oauth, user}` 二次收紧,把 NetMind / Yunwu / OpenRouter
的 openai-protocol row 挡在外面(理由:聚合商只有 chat-completions、不实现
Responses API)。这条限制是 **#81 才加的**;#81 之前(见 git 723d250a→21b31cb9)
codex 能选任意 openai-protocol provider。

删除理由 = **铁律 #15**:这条本质是「平台替用户判断某 provider 不配当 agent
slot」,#15 明确禁止。codex 仍走 `wire_api="responses"`(硬编码,见
[[xyz_codex_official_sdk]]),端点是否真的服务 `/v1/responses` 是 provider 的特性,
运行时才见分晓——跟任何用户自选端点一样,是用户接受的成本,不在配置期拦。

**现在 codex agent slot 只剩 protocol 一道闸**(anthropic provider 仍会被拒)。
连带修掉一个前端 bug:`agentFramework.ts::getModelsForSlot` 以前对**所有** codex
agent slot 强制返回 `CODEX_CURATED_MODELS`,导致选了 netmind 也只看得到 OpenAI
那三个模型。现在 curated 列表**只对 `source=='codex_oauth'`** 生效(与后端
`get_user_config` 的 codex_oauth-only 覆盖对齐),其他 openai provider 暴露自己的
模型列表。三处前端过滤(`AgentLlmConfigPanel` / `ModelDefaultsSettings` /
`CODEX_ALLOWED_PROVIDER_SOURCES` 常量)一并清掉。测试:
`test_agent_slot_service.py::test_codex_framework_accepts_aggregator_source` +
`test_codex_framework_rejects_protocol_mismatch`;
`test_agents_llm_config_routes.py::test_put_codex_accepts_aggregator_openai_provider`。

## 2026-07-09 — validate_slot_binding 放开 helper 的 OAuth(订阅覆盖 helper)

合并 #81 后,`validate_slot_binding` 的 **Rule 3(helper_llm 拒 OAuth)已移除**:helper
现在**接受** claude_oauth / codex_oauth——resolver 把 OAuth helper 路由成
`CliHelperConfig`,`CliHelperSDK` 用同一个 CLI 一次性跑结构化调用(见
`build_cli_helper_config`)。因 #81 把三条规则抽成共享的 `validate_slot_binding`,
删这条同时让 **per-agent override(`AgentSlotService`)** 也一致放开 OAuth helper。

## 2026-07-09 — extracted validate_slot_binding + agent_slots cleanup

The three provider↔slot binding rules (protocol / codex-source / helper-OAuth)
were extracted from ``set_slot`` into a module-level ``validate_slot_binding``,
now the single source of truth shared with [[agent_slot_service]] so a per-agent
override enforces identical rules. ``remove_provider`` also deletes matching
``agent_slots`` rows (by ``provider_id``, globally unique) — else a deleted
provider leaves dangling per-agent overrides that fail at resolve.

## 2026-07-08 — codex OAuth auto-bind: helper 用便宜 mini,不复用旗舰

`add_provider` 的 OAuth auto-bind:claude 分支拆成 `opus`(agent)/`haiku`
(helper);codex 分支原本把 **agent 和 helper 都设成 `curated[0]`**
(`CODEX_CURATED_MODELS[0]` = 旗舰 `gpt-5.5`),导致 helper slot 也被绑成 gpt-5.5。
按设计意图(`_ONBOARD_HELPER_MODELS["openai"] = "gpt-5.4-mini"`,helper 干小结构化
活、便宜快为先),codex helper 应固定为 `gpt-5.4-mini`(在 `CODEX_CURATED_MODELS`
里、ChatGPT 账号验证可用)。修法:agent 仍 `curated[0]`(旗舰),helper 固定
`gpt-5.4-mini`,与 claude 的 opus/haiku 拆分对齐。auto-bind 只填空 slot,故不影响
已绑账户(需手动改或删了重加)。测试见 `test_oauth_dual_slot.py`
(`test_codex_oauth_add_binds_both_slots` 断言 agent=gpt-5.5 / helper=gpt-5.4-mini)。
## 2026-07-07 — netmind inference base env-configurable (minted-key path only)

_build_dual_providers / _verify_onboard_key / add_provider / onboard_one_key gained
an optional inference_base. When set AND card_type=="netmind", the two provider
rows' base_url is derived from it (_netmind_base_for: {base}/anthropic,
{base}/openai/v1) and the pre-write probe uses the same base. Default (None) =
the hardcoded prod _DUAL_PROVIDER_CONFIGS. ONLY use-subscription passes it (from
settings.netmind_inference_base); manual /onboard paste passes nothing → prod.
Rationale: a minted key belongs to the deployment's NetMind env (dev key → dev
inference), but a user-pasted key is their own public prod key. See [[settings]]
and reference/self_notebook/todo/2026-07-03-netmind-inference-base-hardcoded.md.


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

**`CODEX_CURATED_MODELS` 只对 `codex_oauth` 生效（2026-07-10 收窄）**：早期前端 `getModelsForSlot` 对**所有** codex agent slot 一刀切返回这三个模型，理由是"codex CLI 不认非 picker 名字"。但这只对 **OpenAI 自己的 codex 后端(codex_oauth)** 成立——它按账号 tier 网关。第三方 openai-protocol provider(netmind/yunwu/openrouter/custom base_url)有自己的模型目录,codex 把 model 字符串透传给那个端点、由端点决定。所以现在 curated 列表**只在 `source=='codex_oauth'` 时**覆盖(前端 `agentFramework.ts::getModelsForSlot` + 后端 `get_user_config` 两边都是 codex_oauth-only,保持对齐)。

**`codex_cli` 框架只有 protocol 一道闸（无 source 白名单，2026-07-10）**：见文件顶部当日条目。agent slot 只要求 openai protocol;NetMind/Yunwu/OpenRouter 的 openai-protocol row 现在能选。第三方聚合器可能只有 chat-completions、不实现 Responses API,而 codex 硬编码 `wire_api="responses"`——所以运行时可能失败,但这按铁律 #15 是用户/provider 的事,不在配置期拦。OpenRouter 已上线 Responses beta(`/v1/responses`),属于能用;netmind/yunwu 无 Responses 证据,属于选得了、跑起来大概率 404——用户自担。

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

## 2026-07-07 — quick-add 支持换 key（replace 流程）

`add_provider` 加 `replace` 参数：为 True 时跳过 netmind/yunwu/openrouter 的
按-source 唯一性守卫（换 key 时先插新对再删旧对，provider_id 随机不冲突）。

`onboard_one_key` 加 `replace` 参数，仅对聚合类型（netmind/yunwu/openrouter）：
- 已有同源 provider 且 `replace=False` → **不报错**，返回 meta
  `{needs_replace: True, existing_masked, provider_type}`，不改动任何数据，让 UI 弹确认。
- `replace=True`（或本无）→ **expand-contract**：验 key → 加新对（replace=True）→
  两个槽位改指新对 → 删旧对及其槽位。中途失败也不会让用户落到"无 provider"，比
  用户手动"先删后加"更安全。官方 anthropic/openai（source="user"，本就不守卫）不走此逻辑。
背景：过期 key 场景（配合 background-llm 修复）用户要换 key，旧逻辑直接报"已存在"。

## 2026-07-07 (bug#3) — OAuth 登录自动覆盖 helper 槽

`add_provider(claude_oauth/codex_oauth)`：只填**空**的 agent/helper 槽（不覆盖已有配置），一次登录即 可用。`set_slot` 移除了"helper 拒绝 OAuth"的守卫（现在 OAuth 经 CliHelperSDK 服务 helper）。
