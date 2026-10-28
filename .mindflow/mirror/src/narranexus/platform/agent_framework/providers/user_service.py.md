---
code_file: src/narranexus/platform/agent_framework/providers/user_service.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（三）— 保存行 Test 与读取路径共用平台 source 白名单

PR#418 增量 review C1：`test_provider`（保存行；ProviderReadiness 也经由它）不再做
`ProviderSource(row.source)` 的 `ValueError → USER` 转换，而是把行里的**原始** source 字符串作为
`gate_source` 交给 `_probe_api_key_card`，再以 `stored_source=` 传给 [[registry]] `test_provider`，
由唯一判据 `is_platform_provider_source` 决定过不过门。原因：云端免费额度池卡的 source 是
`system` / `system_pool`，枚举里没有，旧转换把它们当 USER，Test 与 readiness 会被门拒掉。
transient `ProviderConfig.source` 现在固定写 USER——它只是个标签，registry 里除出网门外无人读它，
而出网门已改读 `stored_source`。表单探测 `test_provider_config` 传 `gate_source="user"`。

- `get_user_config` 的读取路径筛同样只拦非平台 source；注意本函数构造 `ProviderConfig` 时
  `source` 是严格枚举，`system` / `system_pool` 行若带着本用户的 user_id 进到这里会 ValidationError——
  这是既有行为（这类行 `owner_user_id=NULL`、正常不按用户 user_id 存），本 PR 不改其可见性。

## 2026-10-28（二）— 读取路径筛：已存 user 卡指向内网 → 不可用（云端）

PR#418 review I2：save / Test 有门，但 10/28 之前存下的 `source=user` 行仍可能指向
`http://llm-gateway:4000` / `quota-api` 等。`get_user_config` 组装 `ProviderConfig` 时对每行调
[[registry]] 的 `is_stored_user_base_url_blocked`（仅云端、仅非平台 source——`user`/缺失/真正未知，
平台白名单见「（三）」、
DNS-free `is_obviously_non_public_url`、日志按 provider_id 只打一次），命中则 **`is_active=False`**：

- **选「标记不可用」而不是「跳过」**：跳过会让卡从设置页消失，用户删不掉也看不见原因；
  inactive 则被 `is_user_config_complete`（→ resolver classify 判 NO_PROVIDER）、`validate`、
  绑定校验统一当成不可用。
- 本函数**不是** agent loop 的唯一读取口：真正构建运行配置的
  [[driver/resolver]] `resolve_user_runtime_llm_configs` 按 provider_id 直接读行，per-agent
  override 也不经过这里，所以那边同样调了这道筛（命中抛 `LLMConfigNotConfigured`）。
- 热路径上不做 DNS；解析到内网的多标签名不在本筛覆盖范围，靠 save / Test 的全量门。
- `netmind_free`、`system` / `system_pool` 等平台卡（base_url 本就是内网网关）不受影响；local/desktop 永不拦。
- 测试：`tests/backend/test_provider_egress_gate.py` 读取路径组。

## 2026-10-28 — 出网门：save 时拦 + 保存行测试按**存储的 source** 判定

- `add_provider` 的自定义卡（`anthropic` / `openai`，source=user）在落库前过
  [[registry]] 的 `assert_user_base_url_allowed`（仅云端）。被拒抛 `ValueError` 固定文案
  （路由已有 → 400），**不写行**。理由：存下来的卡之后会被 agent loop / helper LLM /
  Test 按钮反复从服务端去打，内网地址绝不能持久化。云端用户把卡指向**公网**的自建
  OpenAI 兼容端点仍然允许。平台卡（dual-provider 表）base_url 由我们写死，不过门。
- 「更新 base_url」路径不存在（只有 `update_models` 改 models），所以没有第二个 save 点要门。
- 修了一个会让门失效的现象：`test_provider`（保存行）以前把**所有**行都当 `source=USER`
  委托给 `test_provider_config`——那样门要么误杀 free-tier 卡（其 base_url 就是内网网关），
  要么只能放弃按 source 判断。现在 transient ProviderConfig 的构造点收敛到私有
  `_probe_api_key_card(source=...)`：表单探测（`test_provider_config`）固定传 USER（原始
  表单值天然是用户输入），保存行传行里真实的 `source`（「（三）」起改为原始字符串 + 平台白名单判据）；缺失或未知 source 一律当 USER
  （被门的一侧，fail closed）。两道白名单仍只此一处。
- 测试：`tests/backend/test_provider_egress_gate.py`。

## 2026-09-11 — onboarding framework follows plugin availability

`onboard_one_key` still asks `default_framework_for_protocol` which framework a
fresh card lands on; that helper now considers only INSTALLED frameworks, so on
the lightweight local build (no Claude Code / Codex plugin) every card lands on
`nexus_power` instead of an uninstalled framework that could not run a turn.
Only the docstring and the inline comment here changed (they now say
*installed*, and name the nexus_power fallback).

## 2026-09-10 — 订阅集合改 import `SUBSCRIPTION_AUTH_TYPES`（PR#392 复审 I2）

订阅卡 verify 走 CLI 探测的判定原先手写 `("oauth", "oauth_token")` 字面量，改为 import
`narranexus.platform.schema.provider_schema.SUBSCRIPTION_AUTH_TYPES`（唯一定义）。行为不变；将来增删订阅
运输层时这里自动跟随。全仓扫描守卫：`tests/agent_framework/test_claude_fanout_concurrency.py::test_no_consumer_spells_the_subscription_set_by_hand`
（扫 `src/`、`backend/`、`plugins/*/src`；前端手抄件 `lib/agentFramework.ts` 不在射程内）。

## 2026-08-27(review 第 2 轮)— OAuth 三字段收敛到 `_cli_subscription_row_fields`

claude 新建 / claude 原位重连 / codex 新建三处的
`driver_type`/`billing_policy`/`auth_ref` 字面量全部换成模块级 helper,
内部调 `derive.py` 的三个推导函数——本 P1 的根因就是字面量副本漏写其一,
与 `_DUAL_PROVIDER_CONFIGS` 注释记的是同一课。边界:helper **只用于
claude/codex OAuth 卡**;聚合器与 custom 卡刻意不写这三列(resolver 读时
推导+回写),不要在 `_insert_provider` 里做 blanket setdefault。token 行
`derive_auth_ref` 返回 None,helper 里 `or ""` 兜成空串,否则
`_insert_provider` 的 `if key in data` 会跳过该列留下 NULL。

第 5 轮加固 + PR bot 轮修正:helper 对 **scope 外的 source**(非
claude_oauth/codex_oauth)直接 `raise ValueError`。注意判据是 scope
而非"可分类"——`derive_driver_type("user","oauth","anthropic")` 会返回
`custom_anthropic`,按 None 判挡不住 "user",helper 会给不该服务的卡
盖上 external_oauth;PR bot 轮的 raise 测试暴露了这一点。scope 守卫用
derive.py 的 `CLI_SUBSCRIPTION_SOURCES` 共享集合(不再是本地元组副本),
且 **scope 判断与非 None 兜底并列、都要留**:前者挡 scope 外的 source,
后者挡"集合已扩、derive_driver_type 分支未跟"的半成品扩展(否则
`_insert_provider` 会写出 driver_type=NULL)。测试:
`test_cli_subscription_row_fields_raises_on_out_of_scope_source` /
`..._raises_on_half_wired_source`。

**已知取舍(review 第 5 轮 Important 4,待库上核实)**:`test_provider`
的 legacy 兜底(driver_type NULL 时按 **protocol** 推 driver)与真值表
`derive_driver_type`(按 **source** 推)不一致,误路由卡靠两个 driver
里的 source 守卫顶回。更根治的做法是在兜底链里先试
`derive_driver_type`,但那会改变 `source=user + protocol=openai` 遗留行
的路由目标——**改之前必须先在生产库核实不存在"source 非 CLI 源却持有
真实 CLI 凭证"的历史行**(代码推断大概率不存在:两条 OAuth 插入分支
一直写 CLI source、唯一性检查也按 source 查,但这要在库上确认,不能只
从代码推)。核实前维持"守卫双副本 + 协议兜底"现状;接第三个 CLI OAuth
driver(如 gemini_cli)时记得两处都要加:driver 的 source 守卫 +
`test_provider:1104` 的 protocol 三元分支。误路由 seam 有一条不 mock 的
端到端测试钉着
(`test_misrouted_custom_row_gets_source_verdict_through_test_provider`)。

**拒绝过的方案**(review 第 3 轮建议 helper 改收整个 row dict):dict 键
耦合 + reconnect 分支的 update dict 没有 source/protocol 键、还得造兜底,
比显式三参更晦涩。真正的漂移风险(auth_type 表达式在 row 与 helper 调用
两处重复)用局部变量 `oauth_auth_type` 消除即可。route 层探针 stub 的
第三份真值表副本另行修掉(见 backend/routes/providers.py.md 同日条目)。

## 2026-08-27 — host-CLI claude_oauth 行也在插入时写全三字段(P1)

`add_provider(claude_oauth)` 无 token 的 host-CLI 分支此前**不写**
`auth_ref`/`driver_type`/`billing_policy`,靠启动 backfill 下次重启补——
这是 backfill 体系(2026-06-17)落地时漏改的最后一条腿:token 分支
(2026-07-26)和 codex_oauth 分支都早已在插入时写全。窗口症状:配完卡
立刻点 Test,`probe()` 拿到 `auth_ref=NULL` 直接 VERIFY_DEAD,弹
"auth_ref is missing"(P1 工单,dev 频繁重启把这个窗口藏了两个月)。
现在 **claude/codex OAuth 的三条腿**统一插入即写全:host-CLI 写
`CLAUDE_CLI_CREDENTIALS_REF` sentinel,token 写 `""`(token 本身即凭据)。
注意范围:aggregator(netmind/netmind_free/yunwu/openrouter)与
custom anthropic/openai 卡的插入路径**仍不写** driver_type/billing_policy
——那是既定设计,resolver 有读时推导 + 回写,billing_policy 在 schema 与
ProviderCard 双层默认 `user_pays`,api-key 行的 Test 也不走 driver 路径。
backfill 保留——它对 pre-driver 老库升级仍是必要的,只是不再兼任新行的
事实写入者。存量 auth_ref=NULL 的行由 `ProviderCard.from_row` 读时推导
兜底(见 driver/base.py.md 同日条目,review 轮加固:此前文案让用户
"删了重建",而 remove_provider 会连带清光该卡的全部 per-agent 槽位覆盖),
下次启动 backfill 持久化。测试:
`test_claude_oauth_token.py::test_add_claude_oauth_host_cli_row_is_complete_at_insert`。

## 2026-08-06 — OAuth 卡读时覆盖抽到 effective_card_models（口径统一）

`get_user_config` 的 codex_oauth / claude_oauth 内联覆盖分支替换为
`model_catalog.effective_card_models(source, stored)`。**07-30 条目
声称的「覆盖后自愈会修掉死 id 槽位」当时并不成立**：self-heal 挂在
resolver 路径，比对的是 `ProviderCard.from_row` 的裸存量列而非本函数的
覆盖结果——两条加载路径口径分叉，钉旧全 id 的槽位一直判「健康」。
现在两处共用同一函数（self_heal.md 2026-08-06 条目），分叉不可能复发。

## 2026-08-02 — free-tier 开卡用部署配置的模型对 + thinking off（main e1f2acec 的 port）

`onboard_one_key` 里 `ptype == FREE_TIER_SOURCE` 时，agent/helper 的开卡模型
改从 [[free_tier]] 的 `free_tier_default_models()` 取（即 `FREE_TIER_AGENT_MODEL` /
`FREE_TIER_HELPER_MODEL` env），不再用 catalog 常量——此前这两个 env 只进了
provisioner 的日志行，新注册用户仍落在 ops 已弃用的模型上。同时 agent 槽的
`set_slot` 带上 `thinking=free_tier_default_thinking()`（默认 "off"）：钱包
替用户付 reasoning token，且 LiteLLM `/v1/messages` 桥在上游带 reasoning 时
会吞掉整条回复（BerriAI/litellm#29518，2026-08-02 prod 事故）。哪对模型、
开不开 thinking，对钱包卡而言都是**平台掏钱的 ops 决策**，所以从部署配置读。
BYOK 路径不动（thinking 仍传中性 ""）。helper 槽也刻意保持中性：它绑定的是
free-tier 的 OpenAI 协议行，不经 Anthropic `/v1/messages` reasoning 桥，所以
8/2 事故的止血默认只属于 agent 槽。

落库前的 `_verify_onboard_key` 会用同一个部署级 agent 模型发起探测：env 模型
写错会得到 `unverified` 并继续开卡（只有 401/403 会阻断），这是「网络/上游
故障不误杀好 key」的既有策略；ops 应从日志看见并修正错误模型值。修复 8/2
直接落在 main（当天止血），2026-08-04 原样 cherry-pick 进 dev，防止下次快照
切版把它冲掉。

## 2026-07-31 — test_provider:oauth 与 oauth_token 统一走 driver.verify_live

P0「codex 本地 auth 检查无效」的入口层修复:`auth_type=="oauth"` 的
无条件 `True, "OAuth provider (managed by Claude Code CLI)"` 删除——
"CLI 持有真凭证所以 connected 属实"这个 2026-07-28 的论断是错的,CLI
可以持有**过期**凭证;且这个假通过经 [[readiness]] 泄漏,把暂停 job
重新武装到死凭证上。两种 OAuth 现统一:ProviderCard.from_row →
`get_driver_class` → `driver.verify_live()`。缺 driver_type 的存量行按
协议推导(openai→codex_oauth,anthropic→claude_oauth——旧代码缺省
claude_oauth 会把 codex 行验到错误的 CLI 上)。无 verify_live 能力的
驱动回落 probe() 但文案明说"仅存在性检查",绝不把存在性包装成连通性。

Review 轮修正(同日):verify_live 改三态后,本方法负责 verdict→bool
映射(Test 按钮与 readiness 共用):ok→True,dead→False,
**unknown→True + "(not live-verified)"**——False 会把"本节点无法判定"
当成"凭证已死",永久拦死 readiness 的边缘恢复。CODEX_CURATED_MODELS
字面量迁入 [[model_catalog]] 的 _DEFAULT_MODELS,本文件反向读
get_default_models(单一事实源,修 verify_live 的 curated 死代码)。

## 2026-07-31 — 绑定规则第 2 条 + 切框架会清掉跑不了的绑定

`validate_slot_binding` 的 agent 槽位加上「订阅凭据」这一条，调
[[provider_schema]] 的 `framework_can_drive_provider`。两个写入口
（本类的 `set_slot` 与 [[slot_service]] 的 `set_agent_slot`）共用它，一次
收紧两处。**helper_llm 不受影响**——OAuth helper 由 CliHelperSDK 一次性跑
CLI，与 agent 框架无关，所以第 2 条刻意只管 agent 槽。

`set_user_agent_framework` 现在返回 `bool`（是否清了绑定）：切框架**不是**
槽位写入，没有任何别的地方会重新校验它，于是「绑着 Claude Code Login 切到
NexusPower」会把 `validate_slot_binding` 拒绝的那个组合原样留在库里，唯一
的暴露点是 agent 跑到一半。所以这里读一次已绑卡，新框架驱动不了就清空
`provider_id`/`model` 并 warning 一行。

两个刻意的边界：**dangling provider_id 不动**（provider 行已被删的清理归
`remove_provider`，这里再写一次只会跟它抢）；返回值经
`POST /api/providers/agent-framework` 的 `slot_cleared` 传给前端——编辑器据
此同步草稿**和**已保存快照，否则表单会带着一个用户没碰过的空 provider 变
dirty。

## 2026-07-30 — onboarding 的 models 支持按协议 dict（PR #204）

`onboard_one_key`/`_build_dual_providers` 的 `models` 除单列表外接受
`{protocol: [ids]}`——free 门产出的 openai/anthropic 名单确实不同，单列表会
把整卡摊平。dict 中某协议缺失/为空时回落 `get_default_models`。

## 2026-07-30 — claude_oauth 模型列也改为读时覆盖

镜像 codex_oauth 的模式：`get_user_config` 对 claude_oauth 行永远返回 CLI 家族
别名（`get_default_models("claude_oauth","anthropic")` = opus/sonnet/haiku），
无视存量 `models` 列。动机：别名机制上线前建的卡存的是 pinned 完整版本 id，
上游一下线，slot 自愈的 membership 测试恰恰是对着这列做的——死 id 看起来
"合法"，agent 就永远跑在死模型上。覆盖之后自愈会在下次 resolve 把这类 slot
修到活别名。代价：用户不能再自定义该卡模型列（与 codex 一致，前端已隐藏
Edit）。CODEX_CURATED_MODELS 注释补了人工核对 SOP + catalog 一致性 pin 测试。

## 2026-07-29 — `nexus_power` 进 `_SUPPORTED_AGENT_FRAMEWORKS`

框架白名单是双份的(此处 + [[resolver]] 的 `_KNOWN_AGENT_FRAMEWORKS`),两边
都得写。少写任一边的表现不是报错而是**静默回落 claude_code**,槽里存的值
看着完全正常——所以新增框架时这两处一起改,别只改一处。

## 2026-07-28 — 新增 netmind_free 卡型

`_DUAL_PROVIDER_CONFIGS` 多了一个 `netmind_free`，形状和 netmind 卡完全一样
（anthropic + openai 双行、一个 linked_group）。

唯一的特殊处理：它的两个 `base_url` 是**空字符串占位**，实际值在
`_build_dual_providers` 里从 `free_tier.free_tier_endpoints()` 现取。原因是
网关地址随部署变（本地压根没有网关），模块级常量表达不了这种东西。表里仍然
保留这个条目，是为了让 `card_type in _DUAL_PROVIDER_CONFIGS` 这个每条双行路径
都在用的判断保持单一真源。

`onboard_one_key` 的 `allowed` 白名单和「一个 source 一张卡」去重列表同批
加入 —— 后者是防重复开钱包的关键一环。

## 2026-07-26 — claude_oauth 卡支持 setup-token（新建 / 原位重连）

`add_provider(card_type="claude_oauth")` 现在看 `api_key`：空 = 传统
host-CLI oauth 行为不变（含重复卡报错）；非空 = `claude setup-token`
token，`auth_type="oauth_token"`。**已有卡 + token = 原位升级**（改
auth_type/api_key、清 auth_ref、billing_policy=external_oauth、保
provider_id 所以 agent/helper 槽绑定原样存活）——这是 2026-07-23 macOS
Keychain 事故的恢复路径。新建 token 卡在插入时即写全 driver_type/
billing_policy/auth_ref（镜像 codex_oauth 分支），不依赖启动 backfill
（其 auth_ref 推导只覆盖 host-CLI 运输层）。

`test_provider`：`oauth_token` 行路由到
`ClaudeOAuthDriver.verify_token_live()`（真实一次性 CLI 调用）——token
在我们手里所以 Test 按钮可以诚实；`oauth` 行保留静态短路（无凭据可测）。

## 2026-07-23 — `test_provider_config`：无状态「保存前测连通」孪生方法

`test_provider(user_id, provider_id)` 是**有状态**的——先按 provider_id
查 `user_providers` 行再构造 `ProviderConfig`，所以没落库就没法测，添加
表单只能先存后测。新增 `test_provider_config(*, card_type, api_key,
base_url, auth_type, models)`：直接用表单值构造 transient `ProviderConfig`
（provider_id="prov_probe"，never stored），委托同一个
`provider_registry.test_provider`。**不读不写 DB**。非法 card_type
（非 anthropic/openai）直接返回 `(False, ...)`，不碰 registry。两条入口
共用底层 registry、逻辑不重复。供 [[providers]] 路由的 `/test-config` 调用。

**两道白名单守卫（在构造 ProviderConfig 之前）**：`card_type` 只收
anthropic/openai；`auth_type` 只收 api_key/bearer_token。后者非可选防御：
① `oauth` 是合法 `AuthType` 枚举，若透传，registry 的 oauth 短路会
`return (True, "OAuth provider…")`——一次探测都没发生却报连通，纯谎报
（无状态侧没有已落库的 CLI 凭据来支撑这个 True）；② 其他非法 auth_type
会让 pydantic 在构造时抛 `ValidationError`，无全局 handler → 500。守卫把
两者都变成干净的 `(False, ...)`。

**`test_provider`（有状态）已收敛为「读行 → oauth 短路 → 委托
`_probe_api_key_card`（带行的真实 source，2026-10-28 起；此前委托 `test_provider_config`）」**：transient ProviderConfig 的构造点只剩一处，
消除将来漂移。oauth 短路**留在有状态侧**（已落库凭据由 CLI 托管，报连通
属实），正是无状态孪生刻意拒绝 oauth 的镜像。

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
[[official_sdk]]),端点是否真的服务 `/v1/responses` 是 provider 的特性,
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
now the single source of truth shared with [[slot_service]] so a per-agent
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
inference), but a user-pasted key is their own public prod key. See [[settings]]; the hardcoded
inference base is a known follow-up (author-local todo).

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

# providers/user_service.py — 多租户场景的 per-user provider 数据库服务

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

云端部署时，每个用户有自己的 API key 和模型偏好，不能共用单一的 `llm_config.json` 文件。这个服务把 provider 配置从文件系统迁移到数据库的 `user_providers` 和 `user_slots` 表，实现 per-user 隔离。接口设计刻意对齐 `providers/registry.py`，让调用方代码可以相对平滑地切换。

## 上下游关系

被 `api_config.py` 的 `get_user_llm_configs()` 和 `get_agent_owner_llm_configs()` 调用，在每次 agent turn 开始时加载 owner 的 LLM 配置。被 `backend/routes/` 中的 provider 管理 API 路由调用处理用户的 Settings 操作。

在做连接测试时，委托给 `provider_registry.provider_registry.test_provider()`，复用已有的测试逻辑，不重复实现。

`_is_cloud_mode()` 检查 `DATABASE_URL` 是否以 `sqlite` 开头来判断运行模式，但这个函数目前只是辅助判断，不决定哪些代码路径被使用——数据库存储始终被使用，区别在于是否回退到 `llm_config.json`（那个逻辑在 `api_config.py` 的 `_ConfigHolder` 里）。

## 设计决策

**和 `providers/registry.py` 的接口对称**：都有 `add_provider`、`remove_provider`、`set_slot`、`validate_slots`、`test_provider`。这让上层代码可以以相同方式操作两种存储后端，虽然目前没有统一抽象基类（将来可以提取）。

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

**linked_group 机制与 `providers/registry.py` 对应**：删除 provider 时先查 `linked_group`，找到同组所有 provider 一起删除，同时清掉对应的 slots。

**`_DUAL_PROVIDER_CONFIGS` 字典**：把 NetMind/Yunwu/OpenRouter 的双协议配置集中在一个字典里，比 `providers/registry.py` 的三个独立 builder 函数更紧凑，但内容是独立硬编码的，两处不共享。

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

> **2026-08-20 平台默认框架变更**: 无显式选择时的默认 agent framework 由 `claude_code` 改为 `nexus_power`（免费/默认用户跑自研 NexusPower loop；模型不变）。本文件相关默认/兜底串已随之更新。

Merged origin/dev (#382 creation studio, #383 onboarding/profile/import) on 2026-09-06; dev's changes ported onto the new `narranexus.platform` paths.

## 2026-09-07 — 框架事实改从注册表派生（B6）

_SUPPORTED_AGENT_FRAMEWORKS 换成 supported_agent_frameworks()（=available_agent_loop_frameworks，路由层同源）；onboard_one_key 的 provider→framework 硬映射换 default_framework_for_protocol；add_provider 的订阅卡→框架用 framework_for_oauth_source；validate_slot_binding 的错误提示同样。set_slot 在槽位尚无框架时改用 resolve_framework_name()（bound 默认，与 resolver 一致）——旧默认 'claude_code' 是过期硬编码，曾让默认框架用户的 openai 卡被拒绝。

## 2026-09-07（round-2 P2-I2 / P2-I8）— the binding rules moved, and the boundary answers 400

`framework_can_drive_provider` / `get_slot_required_protocols` now come from
`providers/framework_binding` (they were in `schema/provider_schema` and reached up into
`agent_framework` through function-body imports — a layering inversion import-linter could not see).

`set_slot`'s owner-level read calls `framework_binding.resolved_framework_name()` rather than
`driver.resolve_framework_name()`: a `turn.pipeline.act.framework` binding naming a framework no
plugin provides must be a `ValueError` here (the route maps it to 400), not the `RuntimeError` the
route has no handler for. Otherwise the one page that can undo a bad binding answers 500.
