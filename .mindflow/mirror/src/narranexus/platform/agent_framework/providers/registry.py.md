---
code_file: src/narranexus/platform/agent_framework/providers/registry.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（三）— 信任判据改为平台 source 白名单，不再是「枚举成员资格」

PR#418 增量 review C1：上一版把「source 不在 `ProviderSource` 枚举里」一律当 USER（fail closed），
但 `user_providers.source` 的取值域**大于**枚举：云端系统免费额度池卡存成 `system`
（`driver/derive.py` → `system_pool` driver、`system_quota` 计费）或 `system_pool`
（`model_sync.apply_ledger_to_db` 每日刷 models），两者都没有枚举成员，base_url 与
`netmind_free` 同形（内网网关）。结果 runtime builder 会对绑着这类卡的云端用户每一次 agent 运行 /
helper 调用抛 `LLMConfigNotConfigured`。

- 现在唯一判据是模块常量 `PLATFORM_PROVIDER_SOURCES`（6 个平台枚举值 + `system` + `system_pool`）
  与 `is_platform_provider_source()`：在白名单里 → 平台卡、信任；`user` / 缺失 / 真正未知 → 过门
  （fail closed 只覆盖真正未知的值）。读取路径筛 `is_stored_user_base_url_blocked`、
  `test_provider` 的出网门、[[user_service]] 保存行 Test 都走这一个判据，没有第二份枚举转换。
- **显式列出而非从枚举推导**：新增枚举成员默认被门，直到有人确认它是平台卡——fail closed。
  代价是新增平台 source 时要同步这里；漏了的症状是「该平台卡在云端被当用户卡拒」，测试
  `test_cloud_read_screen_trusts_every_platform_source` 列着当前全集。
- `system` 与 `system_pool` 两种拼写是 derive.py 与 model_sync 之间**既有的不一致**，本处两个都收；
  统一它会动计费与 ledger 路径，不在本 PR 范围。
- `test_provider` 新增仅关键字参数 `stored_source`：保存行探测时由它（行里的原始 source 字符串）
  决定是否过门，因为 transient `ProviderConfig.source` 是严格枚举、装不下 `system_pool`；
  不传时按 `provider.source` 判（onboarding / 表单等 transient 配置）。
- `is_stored_user_base_url_blocked` 的 `provider_id` 改为必填（M10：默认空串会让日志去重串台）。
- `_REPORTED_BLOCKED_PROVIDER_IDS` 注释写明是**纯日志去重**（M9）：进程级、不跨 worker 共享、
  **绝不缓存拦截结论**——判定每次重算，别把它改成结论缓存（那就是缓存失败哨兵值）。

## 2026-10-28（二）— review 收口：共享包装 + 读取路径筛

- `assert_user_base_url_allowed` 退化为「空 base_url 直接放行 + 调
  `url_safety.assert_public_http_url_in_cloud`」：部署判定不再在本文件里（review I1）。
- 新增 `is_stored_user_base_url_blocked(source, base_url, provider_id)`（review I2）：**已存库**的
  user 卡（10/28 前存下、没过 save 门的行）在读取路径上的筛。两个调用方：
  [[user_service]] `get_user_config`（命中则卡照常列出、但 `is_active=False`，用户还能看见并删除，
  completeness / 绑定校验 / resolver classify 都当它不可用）与 [[driver/resolver]]
  `resolve_user_runtime_llm_configs`（命中则 `LLMConfigNotConfigured`，不回显 base_url）。
  后者必须单独加：它按 provider_id 直接读行，per-agent override（`agent_slots`）走到它时
  根本不经过 `get_user_config`。
- **只用 DNS-free 的 `is_obviously_non_public_url`**：它在 agent loop 热路径上，每轮都跑，不能解析。
  覆盖字面私网/metadata IP、localhost、`.local`、单标签 docker 服务名（`llm-gateway` / `quota-api`）；
  **不覆盖**解析到内网的多标签名（如 `x.internal`）——那类靠 save / Test 的全量门与离线盘点。
- 判定按**存库 source**：只有不在平台白名单 `PLATFORM_PROVIDER_SOURCES` 里的 source
  （`user` / 缺失 / 真正未知）才筛；`netmind_free` 与 `system` / `system_pool` 的内网网关卡不受影响
  （见「（三）」）。local/desktop 永不拦。
- 日志按 provider_id 只打一次（模块级集合），否则每次读配置都刷屏。
- 测试 `_test_openai_provider` / `_test_anthropic_provider` 内改用 `self._unexpected_status`
  （review M5）；staticmethod `_interpret_test_response` 里保留类名调用。
- 测试：`tests/backend/test_provider_egress_gate.py` 读取路径 5 例（cloud 拒 / local 用 /
  netmind_free 云端照用 / runtime builder 云端拒且不回显 / local 用）。

## 2026-10-28 — 用户 base_url 的出网门（SSRF 整改）

背景：10/28 入侵中，登录用户用 provider「测试连接」让服务端去打 `169.254.169.254`、
`quota-api` / `llm-gateway` / `broker` / `litellm` 等内网目标。

- `assert_user_base_url_allowed(base_url)` 是 provider 层**唯一**的门（save 路径 [[user_service]]
  也调它），内部就是共享的 `url_safety.assert_public_http_url`，不另写检查器。**仅云端**
  （沿用 MCP 存前筛的部署分流先例：桌面版用户合法指向 localhost 的 Ollama / LM Studio）；空 base_url = 官方默认
  主机、非用户可控，不门。
- `test_provider` 在**任何请求之前**按卡的 `source` 判定：只有非平台 source 过门（原为
  `source == USER`，见「（三）」改为平台白名单）。平台卡
  （netmind / netmind_free / yunwu / openrouter）的 base_url 是我们自己写的——free-tier 卡
  本来就指向内网网关 `http://litellm:4000` / `http://llm-gateway:4000`，门它就把免费档打挂。
  **刻意不用 `api_config._is_own_gateway_url` 给用户卡开白名单**：`_OWN_GATEWAY_HOSTS`
  含 `localhost` / `127.0.0.1`，用户卡一旦按它放行就等于把回环 SSRF 放回来。所以用户卡
  指向 `llm-gateway` 一律拒，平台卡靠 source 放行。
- 任何判定失败（不安全 / 解析不了 / URL 畸形）都 fail closed，回固定文案
  `UNSAFE_BASE_URL_MESSAGE`，**绝不回显解析出的内网 IP**（否则门本身成了内网 DNS 探针）。
- httpx 四处调用显式 `follow_redirects=False`（httpx 默认也不跟，写明是为了别人改动时不误开）。
- 不再把上游响应体 / 异常文本回给用户：未识别状态码走 `_unexpected_status` 只回
  `HTTP <code> from the provider`，响应体前 200 字节进日志；异常只回
  `Connection failed (could not reach the provider)`。`_interpret_test_response` 的
  self-serviceable 分类结果是我们自己的词表，保留。
- 未做（后续项）：连接 pinning 到已校验 IP——门只**缩小** DNS-rebinding 窗口，httpx 连接时
  会重新解析。见 [[url_safety]]。
- 测试：`tests/backend/test_provider_egress_gate.py`（含 netmind_free 内网网关卡照常可测 /
  user 卡同 URL 被拒的对照）。

## 2026-07-31 — test_provider 对 OAuth fail closed

瞬态 ProviderConfig 没有 driver 卡,本路径对 OAuth 无从诚实验证,原来
的无条件 True 与 user_service 是同一处谎(同批修复)。改为 False +
指路"存卡后用 Test(live CLI 检查)"。生产上本路径只被 onboarding 的
API-key 探活使用(_verify_onboard_key),不会传 OAuth 配置进来——
fail closed 是防御,不改变现役行为。

## 2026-07-16 — `_interpret_test_response`: 自助类 400/404 不再当"可达"

原逻辑把 400/404/422 一律当"auth 通过=可达"(True)。但 NetMind 余额耗尽是 **400
`balance not enough`**、模型不存在 404、上下文过小 400——这些不是"可运行"。现在该分支复用
`llm.failure.classify_self_serviceable(None, resp.text)`,命中就返回 **False**(单一事实源,
不与 Job/实时层漂移)。这样 `ProviderReadiness.validate` 的实测能正确判"余额死掉=not ready",
edge 恢复(登录/provider 保存)就不会把余额死掉的 job 拉起来白跑一次;充值后实测 200 → 恢复。
onboarding 不受影响(只按 auth 短语硬拒;余额死 key 仍"unverified"通过),"test connection"
按钮则显示真实原因而非误导的 OK。见 `.mindflow/project/references/netmind_billing.md`。

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

# providers/registry.py — 本地单机 LLM provider 配置管理

## 为什么存在

本地单机部署时，用户的 API key 和模型配置需要持久化到磁盘（`~/.nexusagent/llm_config.json`）。Settings 页面支持 5 种 provider "卡片"（NetMind、Yunwu、OpenRouter、Claude OAuth、自定义 Anthropic/OpenAI），每种卡片对应不同的创建逻辑（NetMind 一个 key 生成两个 provider：anthropic 和 openai 协议）。这个文件封装了这些逻辑，提供原子的 add/remove/validate 操作，并包含 provider 连接测试能力。

## 上下游关系

上游：`backend/routes/` 中的 provider API 路由，用于处理前端 Settings 页面的操作。`api_config.py` 的 `_load_from_llm_config()` 在初始化时调用 `provider_registry.load()`。

下游：`~/.nexusagent/llm_config.json` 文件，以及通过 `model_catalog.get_default_models()` 预填充各 provider 的模型列表。

和 `providers/user_service.py` 的关系：这个文件处理**本地单机**场景（`llm_config.json` 文件），`providers/user_service.py` 处理**云端多租户**场景（数据库）。两者接口相似（都有 `add_provider`、`set_slot`、`validate` 等），但存储和作用域不同。`providers/user_service.py` 在做连接测试时会复用这里的 `provider_registry.test_provider()` 方法。

## 设计决策

**linked_group 机制**：NetMind/Yunwu/OpenRouter 一个 API key 对应两个 provider（anthropic 协议 + openai 协议）。通过 `linked_group` 字段把它们关联，删除一个时自动删除另一个，避免孤立 provider 占用 slot。

**Unique 卡片原子替换**：NetMind/Yunwu/OpenRouter/Claude OAuth 是"唯一"卡片，重新添加时先删除旧的（`_remove_by_source`），再创建新的。这样避免多个同类 provider 并存造成混乱。

**连接测试策略分流**：官方端点（OpenAI、Anthropic 官方）用 GET /models（零 token 消耗），非官方代理用 POST 真实 chat completion（max_tokens=1）。`_interpret_test_response` 把 400/404/422 也算作"认证通过"（API 可达、只是 model/payload 问题），这是为了兼容一些代理端点不支持 /models 但认证正常的情况。

## Gotcha / 边界情况

- `add_provider()` 是原子操作（load → modify → save），但在并发情况下（两个请求同时 add）会有 last-write-wins 的竞态。本地单机场景并发罕见，目前可接受。
- Slot 在 `_remove_by_source` 时被清除，这意味着删除 NetMind 后，之前分配给 NetMind provider 的所有 slot 都会失效，用户需要重新分配。

## 新人易踩的坑

- `validate()` 只检查"三个 slot 是否都配置了"，不验证 API key 是否有效。连接测试是独立的 `test_provider()` 操作，不在 validate 流程里。
- `yunwu` 和 `openrouter` 在 `add_provider()` 里的处理方式和 `netmind` 完全相同（都是 unique + dual providers），但在 `_DUAL_PROVIDER_CONFIGS`（`providers/user_service.py` 里）和这里的 builder 函数里有各自独立的 base_url 硬编码，两处要同步维护。

## 2026-09-07（round-2 P2-I8）— `SLOT_REQUIRED_PROTOCOLS` now comes from `providers/framework_binding`

Import site only; the table and its meaning are unchanged. It left `schema/provider_schema` with the
two policy functions that read the framework registry.
