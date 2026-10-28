---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/modeling/profiles.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 自填 id 按名字只借安全的事实

`_with_model_limits`：`get_model_meta` 精确命中（含剥一层路由前缀）时行为不变；未命中时改查
`get_model_name_match`（[[model_catalog]]），只借 `thinks_by_default` 与**更低**的 ceiling（`min`），
**不**借 `vendor_context_window`、**不**抬高 ceiling——同名的自建/量化版本窗口可能更小，借来的墙会让压缩永不触发。

## 2026-09-10（B-03）— 默认思考模型的输出地板 8_192；地板按 `thinks_by_default` 选；`floor_multiplier`

25-45% 平台 job run 空产出的根因：`output_budget` 地板统一 1_024，对默认思考的模型不够——
隐藏 CoT 先吃 max_tokens，1_024 在模型够到文字/工具调用前就被吃光，`stop_reason=max_tokens`
但零产出（NetMind DeepSeek-V4-Pro 实测：1_024→4/6 空跑，4_096→1/6，8_192→0/8）。

- **判据是 `ProviderProfile.thinks_by_default`，不是 `thinking_replay`。** 后者是「这个方言要不要
  回传 reasoning_content」的契约事实，和「这个模型默不默认思考」是两件事；全表只有 deepseek 行是
  `keep`，拿它判地板会漏掉其他默认思考的模型。`_with_model_limits` 从
  `ModelMeta.thinks_by_default` **逐模型**覆盖（不是逐方言），`_PROFILES` 各方言行一律 `False`。
  哪些模型置 `True`、依据是什么，是 [[model_catalog]] 的职责（事实源）；本文件只持有地板常量
  `_THINKING_MIN_OUTPUT_TOKENS = 8_192` 与选择逻辑。
- **clamp 顺序改为 `min(ceiling, max(floor, headroom))`**（原 `max(floor, min(ceiling, headroom))`）。
  地板 1_024 时两者等价；抬到 8_192 后，真实 ceiling 低于地板的模型（DeepSeek-V3 的 7_200）在旧顺序
  下会被顶到 8_192——新顺序保证 ceiling 永远最后钳制。
- **地板刻意压过 headroom**，即使 `input + max_tokens` 因此越过 wall：provider 的可见 400 优于一次
  静默空跑。有实测支撑：同一次 2026-09-08 测量里 128_000 窗口、输入约 122_880、`max_tokens=8_192`
  （合计 131_072，已越墙）8 次全部正常返回——NetMind/DeepSeek 在这一段不强制 `input + max_tokens ≤ window`。
- `requested_max_tokens(profile, extra, input_tokens_estimate, *, floor_multiplier=1) -> int`：一个请求实际携带的
  `max_tokens` 的唯一来源——`extra` 里钉住的值（`int()` 后）胜出，否则 `output_budget`。`None` 或 `int()` 转不了的值
  视为未钉，发计算出的预算而不是 `max_tokens: null`（旧的 `setdefault` 会把显式 `None` 原样发出；平台无人写它）。[[model_client]] 用它发送，
  loop.py 的截断重试用它判断「发了多少 / 翻倍能否更大」。
- `output_budget()` 加 keyword-only `floor_multiplier: int = 1`，只放大地板项，ceiling 仍最后钳制。
  它**不**让结果服从 headroom——地板本来就压过 headroom，放大地板等于放大同一个越墙风险。因此
  loop.py 只把乘数用在截断重试重放的那一步，之后复位为 1（[[loop]] 同日条目）。实算：四个
  `thinks_by_default=True` 的模型 ceiling==地板==8_192，乘数翻倍也不变，重试不可达；能真正翻倍的
  只有非思考行在地板区（如 qwen 1_024→2_048）。

顺带查过：qwen 行的 `context_window=32_000` 是协议猜的，catalog 三条 Qwen3.6 都没填
`context_window`，没有真数字可核对，留着不动。

测试（`tests/nexus_power/test_modeling.py`）：`test_thinks_by_default_gets_a_higher_output_floor`
（负例用 `thinking_replay="strip"` 证明地板不看那个字段）、
`test_thinking_floor_never_exceeds_the_models_own_ceiling`、
`test_catalog_thinks_by_default_overlay_is_honest_per_model`、
`test_self_entered_spellings_of_a_thinking_model_still_think`（自填 id 经 [[model_catalog]] 末段归一化命中）、
`test_client_sends_exactly_requested_max_tokens`。

## 2026-09-08 — deepseek 行 `thinking_replay="keep"`

实测依据见 [[turn_ledger]] 同日条目。按模型族命中是刻意的：`_dialect_profile` 逐行先查 provider 再查 model，
deepseek 行排在 openai 行前面，所以「openai 协议端点上跑 DeepSeek」（NetMind 就是这样）也命中本行——正是出事的形态。
anthropic 协议端点仍取 anthropic 行（strip）：那条路径 CoT 以 thinking block 形式存在，NetMind 桥接侧的 502 属网关问题，
Power 团队 2026-09-08 称已修。

## 2026-07-31 — 方言按协议查,输出上限按模型查(两者的键不同,混用即 bug)

`provider` 参数是 nexus_agent 传下来的**协议名**("anthropic"/"openai"),不是厂商。
所以 `_PROFILES`(cache_style / thinking_replay / supports_arg_delta)按它匹配是对的
——anthropic 协议端点确实吃 cache_control,无论底下服务什么模型;但**一个模型能吐
多少 token 跟它说什么协议毫无关系**。

原先把 max_output_tokens 放进按协议匹配的行里,等于给「所有走 anthropic 协议的模型」
发同一个上限。NetMind 免费档那张卡正是 anthropic 协议却服务 Qwen / DeepSeek / MiMo,
于是 Qwen2.5-7B(32K 窗口)会被要求吐 128K,每个请求必炸。8_192 时代这个错误无害,
抬高上限把它引爆——**这是抬上限时最容易漏的一步**。

现在上限**不由本表提供**,而是 `resolve_profile` 叠加
`providers/model_catalog`——平台唯一的按 model id 索引的事实源,
`adapters/openai_agents` 和 `llm/anthropic_helper` 早就在读它。本地再建一张表就是
同一个问题的第二个答案,而且第一版当场就和 catalog 打架(我写 128_000,catalog 是
115_200)。catalog 的约定是「90% of model limit」,留了固定安全边距;查不到的模型回落
本表的保守默认 8_192。宁可截断(有明确自救路径)也不要整条请求被拒。

副作用是**任何走我们自己 client 的框架都受益**(Owner 2026-07-31 提出的方向):加一个
模型 = 改 catalog 一行,而不是每个调用方各改一行。前缀归一化(`anthropic/xxx` 查不到就
去掉前缀再查)**下沉在 catalog 的 `get_model_meta` 里**,不在本文件——写在调用方等于每个
消费方各抄一份,正是本改造要消灭的模式;而且一次解析出整个 meta 再读两个字段,避免两次
独立查找各自回退、把 A 行的 ceiling 和 B 行的 window 配成一对。

**关键纪律:抬高上限必须同时有实测的 window,压低则不需要。** 删掉 `_MODEL_LIMITS` 时
差点连它的安全属性一起删掉了——「表里没有的模型留在保守默认」。catalog 里有一批行只填了
`max_output_tokens` 没填 `context_window`(GLM-5.1、minimax-m2.7、Kimi、gemini),无差别
overlay 会把它们从 8_192 抬到 58_981~117_964,而 wall 回落到 **anthropic 协议行**的
200_000——那是协议的数字不是这些模型的窗口。这和「编一堵矮墙」是同一个错误,只是方向相反,
而 GLM-5.1 / minimax 就在 NetMind 默认下拉里。压低无条件生效(DeepSeek-V3 真实 7_200
低于默认 8_192,小上限撞不破任何墙)。

## 2026-07-31 — output_budget:给输入留出位置

Anthropic 强制 `input + max_tokens <= context_window`,违反直接 400,而且那句错误
**不含任何 overflow 串表里的标记**,会被判成不可重试的 INVALID_REQUEST 杀掉 turn
(已同批给 `_OVERFLOW_MESSAGE_MARKERS` 补 "context limit"/"exceed context" 兜底)。

dev 网关实测:opus-4-8 吃下 `input_tokens=144065` + `max_tokens=128000` 仍 **200**
——144K+128K=272K 远超本模块管理的 200_000,说明它真实窗口在 1M 级,所以 Opus/Sonnet
这条钳制**永不触发**。真正需要它的是 Haiku:真实窗口就是 200K,而我们的压缩要到
150K 才触发,中间留了一段「未压缩但已超限」的带。

钳制读 `profile.output_wall`(未实测则回落到 context_window),**不读
`vendor_context_window` 原始值**——见 contracts/model mirror,那里记着字面量默认值造成
分叉、把免费档默认模型自己压到 1_024 的那次。

因此 ProviderProfile 分了两个窗口字段,不是冗余:`context_window` 是我们**选择**管理
和压缩的预算,`vendor_context_window` 是请求会 400 的**硬墙**。分开才能让钳制用真实
墙、同时不动压缩阈值(把 Opus 的 context_window 直接改成 1M 会让压缩推迟到 750K,
是本 PR 范围外的行为变更)。

# modeling/profiles — ProviderProfile 数据表

接入新 provider=加一行;字段只录实测方言,不做模型价值判断(铁律 #15)。未知→保守默认行(可跑,无优化)。claude 别名兜底进 anthropic 行。
