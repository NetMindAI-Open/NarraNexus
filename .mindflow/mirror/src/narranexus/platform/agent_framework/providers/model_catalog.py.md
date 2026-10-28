---
code_file: src/narranexus/platform/agent_framework/providers/model_catalog.py
last_verified: 2026-10-16
stub: false
---

## 2026-10-16 (PR#413 review I1+I2+M4) — onboarding 默认的唯一服务端事实来源

`get_onboard_default_model(source, slot_name)` 是"平台把一张卡开在哪个模型"的**唯一**来源:onboarding(`get_default_agent_model` /
`get_default_helper_model` 现在也走它)、slot self-heal、`GET /api/providers/catalog` 的 `onboard_defaults`(`get_onboard_defaults()`)三处共用。
`netmind_free` 一支走 `free_tier.free_tier_default_models()`(env `FREE_TIER_AGENT_MODEL` / `FREE_TIER_HELPER_MODEL`,空串回落常量),
所以 ops 改 env 后,免费卡的自愈目标与 UI 推荐同步跟随;`free_tier` ↔ `model_catalog` 成环,故函数内 lazy import(同 `user_service` 先例)。
`get_onboard_defaults()` 只输出**来源**键(排除 `anthropic`/`openai` 这两个协议键:官方 BYOK 卡的 source 是 `user`),避免两种键空间混用。
`get_default_*_model` 的参数名 `protocol` → `provider_type`;槽位名用 `SlotName` 枚举而非字面量。
上文 "2026-10-16 (预审修订)" 里"未知来源返回 None 即卡序行为"仍成立。

## 2026-10-16 (预审修订) — 离线账本 V4.1-Flash 条目取自线上库

`model_probe_ledger.json` 里手加的 V4.1-Flash 条目抄自 prod DB 账本(anthropic/openai=pass、context=1M、tested_at=2026-10-13T05:00:17Z),
并非本仓探测。`get_onboard_default_model` 用于 self-heal 时,目录默认不再参与(见 [[derive]]):未知来源返回 None 即"卡序行为"。

## 2026-10-16 — NetMind 默认改 DeepSeek-V4.1-Flash(agent 与 helper 同款)+ get_onboard_default_model

Owner 决定:netmind / netmind_free 的 agent 与 helper_llm 默认均为 `deepseek-ai/DeepSeek-V4.1-Flash`(此前 agent=V4-Pro)。
原因:V4-Flash 约 2026-09-11 从 NetMind 公开目录消失(探针账本仅作 `extra`,不上 Power 卡),槽位因此被 self-heal 翻到卡表头
omni 模型;V4.1-Flash 在目录内且双协议通过。V4.1-Flash 已登记 `ModelMeta`(`thinks_by_default=True`,沿用 V4 家族,
未单独实测;偏保守方向,只会抬高输出预算下限)并加入 `_DEFAULT_MODELS` 与离线账本。
新增 `get_onboard_default_model(source, slot_name)`:无回退(未知来源返回 None),供 self-heal 判断平台对该卡是否有默认,
与带回退的 `get_default_agent_model` / `get_default_helper_model` 区分。官方家族(anthropic/openai/yunwu/openrouter)agent 仍取最强款。
下文 2026-06-10 条目里 "netmind = V4-Pro / V4-Flash" 已过时。

## 2026-09-10 — 订阅集合改 import `SUBSCRIPTION_AUTH_TYPES`（PR#392 复审 I2）

CLI 家族别名原样保留的判定原先手写 `("oauth", "oauth_token")` 字面量，改为 import
`narranexus.platform.schema.provider_schema.SUBSCRIPTION_AUTH_TYPES`（唯一定义）。行为不变；将来增删订阅
运输层时这里自动跟随。全仓扫描守卫：`tests/agent_framework/test_claude_fanout_concurrency.py::test_no_consumer_spells_the_subscription_set_by_hand`
（扫 `src/`、`backend/`、`plugins/*/src`；前端手抄件 `lib/agentFramework.ts` 不在射程内）。
## 2026-09-11 — 自填 id 的按名匹配：`get_model_name_match`（与身份查找分开）

`get_model_meta` 仍是**身份查找**：精确 id → 剥一层前缀，别无回退；`get_max_output_tokens` /
`get_context_window` / `get_model_display_name` 因此也都不猜。
新增 `get_model_name_match(model_id) -> Optional[ModelNameMatch]`：按 id 的**最后一段**（小写）查
`_KNOWN_MODELS_BY_NAME`（`_register` 同步维护），让 BYOK / 自填 base_url 写成 `deepseek-v4-pro`、`DeepSeek-V4-Pro`、
`netmind/deepseek-ai/DeepSeek-V4-Pro` 也能拿到 `thinks_by_default`（否则 B-03 对手填用户复现）。
`ModelNameMatch` 只有 `thinks_by_default` 与 `max_output_tokens` 两个字段——**没有** model_id / display_name
（那是别的行的）也**没有** context_window（同名不同模型，借来的墙会错）；`max_output_tokens` 只允许消费方用来
**降低**自己的 ceiling。只有同名所有行在这两个字段上一致才返回，不一致即歧义 None；不做模糊匹配
（`deepseek-v4-pro-experimental` 仍未知）。唯一消费方：nexus_power [[profiles]] `_with_model_limits`。
测试在 `tests/nexus_power/test_modeling.py`（三种拼法正例 + 未知/非思考/近似名负例 + 歧义负例 + 不借窗口不抬 ceiling + 可降 ceiling）。

## 2026-09-10（B-03）— `ModelMeta.thinks_by_default`

新增 `thinks_by_default: bool = False`，本表是这个事实的唯一来源；消费方是 nexus_power
`profiles._with_model_limits`（逐模型覆盖到 `ProviderProfile`），地板常量与选择逻辑在 profiles.py，
**不在本文件**。取值纪律同本表对 ceiling 一贯的规矩：只写有实测或公开无争议的事实，其余留 `False`，
每行旁注明口径。

- `True`：DeepSeek-V4-Pro / V4-Flash（本仓 2026-09-08 对 NetMind OpenAI 协议端点实测）；
  o3 / o4-mini（OpenAI o 系没有「关思考」模式）。
- 留 `False`：DeepSeek-V3（旧线，不在那次实测里，不按家族名推断）；Gemini 3.1、Kimi K2.5/K2.6、
  GLM-5/5.1、MiniMax M2.7、Qwen3.6（无本仓实测）；gpt-5.x（思考档位可调，「是否默认花思考级预算」
  是需要实测的定量问题）。

注意：四个 `True` 行都没登记 `max_output_tokens`，ceiling 取方言默认 8_192，恰等于思考地板——
所以 loop.py 的截断翻倍重试对它们永远不触发，修好它们的是地板本身。

## 2026-08-06 — effective_card_models：卡片运行时模型列的唯一口径

新增 `effective_card_models(source, stored)`：claude_oauth → 别名表、
codex_oauth → curated 表、其余 → 存量列。动机（Base recvqEiNbacKWa
「本地CC模型name不自动更新」收口）：读时覆盖此前只活在
`get_user_config` 内联分支里，resolver 路径经 `ProviderCard.from_row`
拿裸列 → self-heal 的成员测试对裸列跑，钉在旧全 id 的槽位判「健康」
永不自愈。两条加载路径（user_service / self_heal）现在共用本函数，
口径不可能再分叉。同批新增 `claude_family_alias(model_id)`：
`claude-sonnet-4-6`→`sonnet`（self-heal 家族保持用）；带聚合商前缀
（`anthropic/claude-*`）返回 None——那是 API-proxy 卡，全 id 是对的。

## 2026-07-31 — codex curated 列表迁入 _DEFAULT_MODELS(单一事实源)

`("codex_oauth","openai")` 键新增,值即原 user_service 里的
CODEX_CURATED_MODELS 字面量;user_service 现在反向读
`get_default_models("codex_oauth","openai")`。动机(PR #224 review 第
2 条):字面量留在 user_service 时 catalog 查不到 codex 键,
codex verify_live 的「curated 优先」防线是死代码,永远回落到它本要
提防的存量 models 列。人工核对 SOP 与 pin 测试
(test_codex_curated_models_stay_registered_in_catalog)不变。

## 2026-07-31 — ModelMeta 增 context_window;nexus_power 也接进来了

新增可选 `context_window` + `get_context_window(model_id)`:provider 的**硬墙**
(`input + max_tokens` 超了就 400),不是我们自选的预算。只有「按输入剩余空间决定输出
上限」的调用方才需要它;`None`=未核实,调用方必须回落到自己的预算——**编一堵比真实墙
矮的墙,会把靠近它的每个请求悄悄压小**(nexus_power 就这么把免费档默认模型自己压到
1_024 过)。

同批填上:Claude 三行的 context_window(opus/sonnet 1M、haiku 200K),以及 haiku 一直
空着的 `max_output_tokens=57600`(=90% × 64_000)。dev 网关实测:
opus-4-8 吃下 144_065 input + 128_000 max_tokens 仍 200;haiku@64000→200、
haiku@128000→**400**。

新增 `get_model_meta(model_id)`:**带路由前缀回退的唯一入口**,`get_max_output_tokens` /
`get_context_window` 都改走它。前缀归一化必须在这里而不是调用方——写在调用方就是每家抄
一份(nexus_power 先这么干过,结果 anthropic_helper 拿平台 id 仍查不到);而且需要两个字段
的调用方应当**一次解析 meta**,否则两次独立查找各自回退,可能把 A 行的 ceiling 配上 B 行
的 window。`get_all_known_models()` 同步带上 `context_window`,否则前端从
`/api/providers/catalog` 拿不到新字段。

**回退下沉的副作用要一起记住,不是只有收益**:`get_max_output_tokens` 自带回退之后,
另外两个消费方的取值跟着变了。catalog 里「裸名且有 ceiling」的只有四条 Claude
(`claude-opus-4-8`/`claude-sonnet-4-6`=115_200、`claude-haiku-4-5(-20251001)`=57_600),
所以实际影响面是 `yunwu/claude-*`、`openrouter/claude-*` 这类聚合商路由 id 在
`llm/anthropic_helper` 里从 `_DEFAULT_MAX_TOKENS=4096` 跳到 115_200/57_600。方向是对的
(那是 Anthropic 真实上限),但**这两个消费方没有 profiles 那套「抬高需实测 window」的
护栏**,它们直接拿 ceiling 当 max_tokens 用。万一某个聚合商对 claude 的 max_tokens
卡得比原厂低,原先稳过的 4096 请求会变 400。影响面限于 helper 类原子调用(不在
agent_loop 上),暂不加护栏,但下次聚合商报 400 先想到这里。

消费方从两个变成三个:`adapters/openai_agents`、`llm/anthropic_helper`,加上
`nexus_power/_nexus_power_impl/modeling/profiles.py`。**新增/修改模型上限只改这里**,
别在框架侧再建表(2026-07-31 有过一次,当场和本表的 115_200 对不上)。

## 2026-07-30 — `get_default_models("netmind_free")` 先读网关门后的条目

每日 pass 把「网关∩判定」写成 ledger 的 netmind_free 条目后，新 free 卡的
种子从它取——裸 netmind 通过名单含网关不路由/不计价的模型（sonnet-5 曾借此
混进 free 下拉）。条目缺失（首次 pass 前的新装）才回落 netmind 映射。

## 2026-07-26 — `resolve_cli_alias` 把 `oauth_token` 并入 CLI 侧

setup-token 运输层同样经 claude CLI 跑（不是 raw Messages API），家族
alias（"opus"）由 CLI 自解析——与 `oauth` 同待遇保留 verbatim，避免在
我们代码里陈化。

## 2026-07-03 — `resolve_cli_alias` (upstream #57)

New `_CLI_ALIAS_TO_MODEL_ID` map + `resolve_cli_alias(model_id, auth_type)`
next to the alias ModelMeta registrations. CLI family aliases ("opus") are
only valid on the OAuth/CLI path; raw Anthropic-compatible APIs 400 on them
and the runtime surfaces no_reply. Model strings are free text end to end
(no backend catalog validation), so normalization lives at the transport
boundary: non-OAuth → full id, OAuth → verbatim (the CLI resolves "latest
of family" itself, keeping it un-stale). When a family ships a new latest,
update the map with the ModelMeta entries —
tests/agent_framework/test_model_alias_normalization.py guards that map
targets are registered catalog ids.

## 2026-06-10 (later) — onboarding defaults for aggregator sources

_ONBOARD_AGENT/HELPER_MODELS gained netmind (DeepSeek-V4-Pro / V4-Flash,
matching the old Quick Add preset pair) and yunwu/openrouter
(claude-opus-4-8 / gpt-5.4-mini — they proxy the official APIs).

## 2026-06-10 — one-key onboarding default models

`get_default_agent_model(protocol)` / `get_default_helper_model(protocol)`:
agent = strongest of the family (anthropic → claude-opus-4-8, openai →
gpt-5.5) because BYOK users pay themselves; helper = cheap+fast (haiku-4-5 /
gpt-5.4-mini). All four ids already exist in the suggested lists, so
self-heal never rewrites them.

# model_catalog.py — 静态模型元数据与默认配置库

## 为什么存在

Settings 页面需要知道"NetMind 支持哪些模型"、"text-embedding-3-large 的维度是多少"、"GPT-5.1 的最大输出 token 是多少"。这些信息如果分散在各个 SDK 文件里会很难维护。这个文件把所有已知模型的元数据（维度、最大 token）和各 provider 的默认模型列表集中管理，供 `providers/registry.py`、`providers/user_service.py`、`adapters/openai_agents.py` 以及前端 API 查询。

## 上下游关系

被 `providers/registry.py` 调用来预填充新 provider 的模型列表（`get_default_models(source, protocol)`）。被 `adapters/openai_agents.py` 调用来获取 `max_output_tokens`，避免超出模型限制。被 `backend/routes/` 的 provider 相关 API 路由调用来返回 embedding model 列表和 suggested model 列表给前端。

无下游依赖——这是一个纯数据文件，不 import 任何其他系统模块。

## 设计决策

**纯静态数据，不查询 API**：不做动态 model discovery（如调用 OpenAI `/models` 接口），避免在初始化路径上引入网络依赖。代价是模型列表需要手动维护，新模型上线后要更新这个文件。

**按 (source, protocol) 二维键组织默认模型**：NetMind 的 Anthropic 和 OpenAI 协议支持的模型列表是完全不同的，不能只按 source 组织。这个设计让 `provider_registry` 在创建 provider 时能准确预填充正确协议的模型。

**`max_output_tokens` 设为模型上限的 90% 左右**：注释里说明了这个值是"90% of model limit"，留了安全边距，避免因提示词稍长而频繁触发截断错误。新加的 model 如果没有"独立验证"过的 token 上限，**留 None** —— 调用方会回退到 provider 自己的 cap，比胡乱填更安全。

**`is_official_provider()` 检查用于测试策略分流**：`providers/registry.py` 在做连接测试时，官方端点用 GET /models（零 token 消耗），非官方端点用 POST 真实 chat completion 请求（min token）。这个分流依赖 `OFFICIAL_BASE_URLS` 字典。

## Gotcha / 边界情况

- 如果用户配置了 catalog 里没有的 model，`get_max_output_tokens()` 返回 `None`，`adapters/openai_agents.py` 会不传 `max_tokens` 参数给 API（让 API 用默认值）。这是安全降级，不是错误。
- `get_official_models()` 和 `get_suggested_models()` 都查同一个 `_SUGGESTED_MODELS` 字典，返回结果相同，只是语义名称不同（给不同调用场景的 API 用）。

## 新人易踩的坑

- 新增 provider 预设时（如新的 proxy 服务），需要同时在 `_DEFAULT_MODELS`、`providers/registry.py` 的 builder 函数、`providers/user_service.py` 的 `_DUAL_PROVIDER_CONFIGS` 三处同步更新。这三处没有共享常量，容易遗漏一处。
- `ModelMeta.max_output_tokens` 单位是 token，不是字符，但名称容易让人混淆。`adapters/openai_agents.py` 把这个值传给 `max_completion_tokens` 参数。

## 新增一个 NetMind / 类似 provider 的 model — 三步 SOP

这是改动这个文件最常见的场景。按这个顺序操作不会漏：

1. **注册元数据**：在对应 provider 的 `_register(ModelMeta(...))` 块里加一行。`max_output_tokens` 没核实过就留空。
2. **加进默认列表**：在 `_DEFAULT_MODELS[(source, protocol)]` 对应的 list 里追加 model_id。注意一个 model 可能同时出现在 `("netmind", "openai")` 和 `("netmind", "anthropic")` 两个键下，按需要加哪个就加哪个。
3. **同步老用户**（如果该 provider 已经有用户在用）：跑

   ```bash
   uv run python scripts/data_migrations/backfill_netmind_default_models.py --dry-run   # 预览
   uv run python scripts/data_migrations/backfill_netmind_default_models.py             # 写入
   ```

   脚本是幂等的——已经包含了的 model 会被识别为 `[OK]` 跳过；只追加缺失项到 `models` JSON 数组末尾。该脚本目前硬编码 `source="netmind"` + 双协议遍历；如果以后要给 yunwu / openrouter 也做同样的事，复制这个脚本改 source 即可。

后端**必须重起**才能让新 model 的元数据进入 catalog 缓存——`_KNOWN_MODELS` 和 `_DEFAULT_MODELS` 是模块级的，import 时初始化一次。前端再刷一下页面，Settings 下拉就能看到新 model。

## 2026-07-07 — is_cli_family_alias

新增 `is_cli_family_alias(model_id)`(即 `_CLI_ALIAS_TO_MODEL_ID` 成员判定),供 `to_cli_env` 判断 DEFAULT 重定向是否会自指(别名进重定向会让 CLI 拒启)。
