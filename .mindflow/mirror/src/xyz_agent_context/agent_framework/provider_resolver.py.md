---
code_file: src/xyz_agent_context/agent_framework/provider_resolver.py
stub: false
last_verified: 2026-07-20
---

## 2026-07-20 (续) — 文案"退出重登"改为"Settings → Account 里接入"

use-subscription 有了第一个前端调用方（[[NetmindAccountPanel]] 的
Link it now 按钮 + 订阅支付后自动接入），"then sign out and back in"引导
随之改为指向面板按钮；load-bearing 注释第 2 条同步（"no frontend calls"
已不成立）。**前缀 "Free quota exhausted." 原样保留**（job_trigger 第三层
探测契约，test_no_quota_pause 钉住，改后实跑确认仍绿）。

## 2026-07-20 — QuotaExceededError 文案补「订阅 NetMind.AI 套餐」

`NETMIND_USE_SUBSCRIPTION_ENABLED` 于 2026-07-20 在 prod 打开后，登录会自动在
用户自己的 NetMind 账户下生成 key 并绑定槽位。于是出现一类新用户：**被绑上了
NetMind key，但从未订阅过套餐、账户没有余额**。他们免费额度耗尽后走 1b 自动
迁移，切到那把空 key —— 原文案只说"配置你自己的 provider"，对这类人是死路，
因为他们已经有 provider 了，缺的是余额/订阅。

文案改为同时给出两条出路：加 provider **或** 订阅 NetMind.AI 套餐。

⚠️ **订阅那半句必须带上「重新登录」**（review 指出的实质问题）。订阅本身**不产生
provider** —— `ensure_netmind_provider` 只在登录路径（`auth.py`）和
`POST /providers/use-subscription` 上跑，而后者**前端没有任何调用方**
（`api.useSubscription()` 只有定义）。所以只说"去订阅"会让用户付完钱、重试、
撞回同一个 402，比不给这个选项更糟。

> 这背后是个**产品缺口**而不只是文案问题：flag 打开后，用户被自动接入的**唯一**
> 途径仍然是重新登录。把 use-subscription 按钮在前端接上，才是这条路的正解。

⚠️ **"Free quota exhausted." 这个前缀短语不能动**：`job_trigger` 的
`_NO_QUOTA_ERROR_MARKERS`（[[job_trigger]]）把它作为第三层 legacy 子串检测，
用来把后台 job 置为 PAUSED_NO_QUOTA 而不是无限重试（上游事故：9 用户 / 14 天 /
390 次重试）。

该契约**现在有测试守着**了：`tests/job_module/test_no_quota_pause.py` 新增
`test_real_resolver_messages_still_pause_jobs`，构造**真实异常**再断言
`_is_no_quota_failure` 命中。此前那条测试写的是硬编码字面串，与真实异常无关联
——改文案它照样绿，等于没有保护。新测试经变异验证：破坏前缀即变红。

## 2026-07-18 — 免费额度优先成为平台行为（用户偏好删除，决策树无状态化）

Owner 决策：用户不再能选择"用不用免费额度"。classify 的决策树从"读偏好分流"
简化为纯状态判断：**有 quota 行且有余量 → SYSTEM_OK；耗尽 + 有自有 key →
USER_OK；耗尽 + 无 key → QUOTA_EXCEEDED；无 quota 行 → 只走自有 key**。
连带变化：

- `prefer_system_override` 列**重定义为耗尽通知闩锁**（armed=1/fired=0）：
  耗尽首跑 CAS 1→0 发一次"已切到你自己的 key"通知（#48 去重保留）；下次
  有余量的运行 0→1 重新武装（`quota_svc.rearm_switch_notice`，仅 0→1 边沿
  写库）。**补充额度后自动回到免费额度**——旧闩锁语义下要手动重开，现在不用。
  **rearm 是 best-effort**（review 修复 2026-07-18）：它落在 SYSTEM_OK 成功
  路径上，装饰性写入抛异常绝不能挡有余量的运行——try/except + warning，与
  classify"不抛异常"契约及本文件其他 DB 调用的兜底模式一致；回归测试
  `test_rearm_failure_never_blocks_a_budgeted_run`。rearm 故意非 CAS（幂等
  无副作用；若将来挂副作用需先加 CAS，见 quota_service docstring）。
- `FREE_TIER_EXHAUSTED` 判定 + `FreeTierExhaustedError`
  （错误码 FREE_TIER_EXHAUSTED_DISABLE_TOGGLE）删除——#48 后本就是死分支，
  文案还在指引用户关一个已不存在的开关。`NoProviderConfiguredError` 的
  用户文案与 docstring 同步去开关化（review 二轮抓出："enable 'Use free
  quota'"指向死路），现为"Add a provider in Settings to continue."。
- "opt-out 必须被尊重"的旧不变量作废；"无 quota 行绝不隐式授予免费额度"
  （无界负债守卫）**保留**。

## 2026-07-09 — resolve_and_set 串 cli_helper(订阅覆盖 helper)

`resolve_and_set` 的 `set_user_config(...)` 增传 `cfgs.cli_helper`,让"订阅覆盖
Helper LLM"的 CLI-backed helper 在**这条统一解析路径**上也被激活——即 HTTP 请求路径
(auth 中间件)和后台注入原语(`resolve_and_set_provider_for_user` →
`inject_owner_helper_credentials`)拿到 OAuth helper 时,`get_helper_sdk` 能 dispatch
到 `cli`。与 `RuntimeLLMConfigs.cli_helper`(见 [[api_config]])一致。

## 2026-07-09 — agent_id on resolve / resolve_and_set / helper injection

``ProviderResolver.resolve`` + ``resolve_and_set`` gained optional ``agent_id``,
passed into ``resolve_user_runtime_llm_configs`` on the USER_OK / SYSTEM_DISABLED
own-config branches (SYSTEM free-tier ignores it). ``resolve_and_set_provider_for_user``
and ``inject_owner_helper_credentials`` thread it too — the memory-consolidation
worker's detached helper task for agent A now overlays A's per-agent helper
override (helper follows its agent), not just the owner default.

## 2026-07-08 — 后台 helper 在 SYSTEM_DISABLED 下兜底到 user config

`resolve()` 在系统免费层禁用(本地/desktop 模式)时返回 None,`resolve_and_set`
原本一律 strict no-op。请求路径(auth 中间件)靠这个 no-op 保留全局/desktop 配置,
是对的。但 `inject_owner_helper_credentials`(detached hook 的 helper 注入,#68)
**先 `clear_user_config()`** 再走后台孪生函数 `resolve_and_set_provider_for_user`
→ no-op 把 helper 配置留成**空**。于是所有后台 LLM hook(记忆抽取 / 社交实体摘要 /
叙事更新)对**配了自己 provider(如 NetMind)**的用户,裸打空 key 的官方
`api.openai.com` → 401。主 agent 回复不受影响,因为 agent-loop 路径
(`get_user_runtime_llm_configs`)对 None 已有兜底到 user config。

修法:`resolve_and_set` 加 `own_config_when_system_disabled`。请求路径保持默认
no-op;后台孪生传 True,在 None 时 fall through 到
`resolve_user_runtime_llm_configs`(用户自己的 provider)——与 agent-loop 路径一致。
真实 NetMind 环境验证:注入后 helper 带上 NetMind 的 openai key + base_url + model,
不再是空默认。

**异常契约(review 抓到的坑)**:`resolve_user_runtime_llm_configs` 在无可用配置时抛
`LLMConfigNotConfigured`(属 `LLMResolverError`/`RuntimeError`),而后台三个调用方
(narrative updater / `_run_hooks_background` / memory worker)catch 的是**互不相交**的
`ProviderResolverError` 家族。若不翻译,unhappy path(SYSTEM_DISABLED + 无 own config)
会绕过 `except ProviderResolverError` 的凭证告警,落进泛 `except` → agent_runtime **继续
跑 hook 且退回全局平台 key**——正是这套机制要防的 2026-07 事故。所以 fall-through 里把
`LLMConfigNotConfigured` 翻译成 `NoProviderConfiguredError`(方向与 api_config 里
`ProviderResolverError → SystemDefaultUnavailable` 互为镜像),保持调用方异常契约不变。

## 2026-07-07 — auto-switch 改竞态安全 CAS + 一次性通知(#48)

`classify()` 的「用尽 + 有 own provider → 关免费层偏好 → USER_OK」分支,原本
无条件 `set_preference(user_id, False)`(每次都写、并发多写)。改为
`quota_svc.disable_preference_if_enabled(user_id)` —— 底层
`UPDATE … SET prefer=0 WHERE prefer=1` 的 compare-and-swap:并发请求里**只有
一个**拿到 rowcount>0(赢得 1→0 翻转),它才调 `_emit_free_tier_switch_notice`
写一条 `SYSTEM_NOTICE`(source.type `free_tier_switch`,前端 App.tsx 弹一次性
banner 再 mark-read)。通知是 best-effort:失败只 warn、绝不拖垮已切到用户
key 的 run。**为什么这里是唯一的 flip 点**:`classify()` 是全项目单一决策树,
agent-run 路径(api_config)现已收敛到它(见 api_config.py.md 2026-07-07),所以
HTTP / 后台 job·bus / lark 全部共享同一次性 auto-switch。

## 2026-06-17 — resolve() 并入单点 resolver,不再用 protocol-blind builder

`ProviderResolver` 是"配额/系统默认 vs 用户自配"的**决策树**(classify),但它
原来还自带一份 protocol-blind 的 config builder(`_llm_config_to_dataclasses`,
返回 2-tuple,不认 anthropic_helper / codex)——这是 anthropic-helper 后台固化
bug 的根。本轮:
- `resolve()` 的 USER 分支改调单点 `resolve_user_runtime_llm_configs`(用注入的
  `user_provider_svc.db`,DI 不走全局),返回 `(RuntimeLLMConfigs, source)`。
- `resolve_and_set` 用 **4 参** `set_user_config(claude, openai, codex,
  anthropic_helper)`,所以 HTTP 请求路径(auth.py)和后台 consolidation worker
  都正确装上 helper 协议 + codex。
- `_llm_config_to_dataclasses` 仅留给 SYSTEM/free-tier(受控 openai 形状),
  docstring 已标注;USER 永不走它。
- classify 的完整性判断仍读 `user_provider_svc.get_user_config`,与 resolve 的
  config 构建解耦(决策 vs 构建)。
这一步把"slot→config"的三份拷贝收敛到一份(另两份:api_config legacy fallback
本 PR 已删;resolver if 阶梯已多态化)。

# Intent

Single arbiter that decides which LLMConfig feeds a run and whether quota
bookkeeping applies. The decision is now factored into **one verdict-only
classifier** (`ProviderResolver.classify` → `ProviderAvailability`) so every
caller that needs "can this user resolve a usable provider right now" shares
the exact same tree:

- HTTP request path: `resolve` / `resolve_and_set` (auth_middleware) maps the
  verdict to three dataclasses + ContextVars, or to a `ProviderResolverError`.
- Job resume gate: `JobTrigger._user_can_run` maps the verdict via
  `is_runnable` (through the `classify_provider_for_user` wiring helper).

**Why one classifier (2026-06-01):** the resume gate used to reimplement the
tree as "quota OR own-provider-complete" and drifted — it ignored
`prefer_system_override`, so a user opted in to an exhausted free tier who also
had an own provider was judged runnable, resumed, then rejected by the runtime
(which will NOT silently spend their own key), forever. That was the 2026-05-31
prod pause/resume oscillation. Extracting `classify` makes the gate and the
runtime physically incapable of disagreeing.

## 2026-06-11 — resolve_and_set_provider_for_user

Module-level twin of `classify_provider_for_user` that also SETS the
ContextVars — for background jobs outside the HTTP request path (memory
consolidation worker). Same decision tree, same exceptions; local mode
is a strict no-op.

## The decision tree (`classify`)

Keyed on the user's `prefer_system_override` Settings toggle — the single
source of truth — NOT on whether an own config happens to exist:

0. `is_enabled() == False` -> `SYSTEM_DISABLED` (strict no-op; must not even
   call `quota_svc.get` / `get_user_config`). Local mode / feature-off stays on
   the `llm_config.json` global fallback; `resolve` returns `None`, the resume
   gate treats it as runnable (not gated).
1. `prefer_system_override == True` (default for new users):
   1a. `quota_svc.check()` -> `SYSTEM_OK` (route system; cost_tracker deducts).
   1b. no budget + complete own config -> **auto-disable + `USER_OK`** (#48):
       `classify` calls `quota_svc.set_preference(user_id, False)` (persists
       `prefer_system_override=False`) then returns `USER_OK`, routing the
       request on the user's own key. This is the one deliberate write
       side-effect inside `classify`. `FreeTierExhaustedError` and the
       `FREE_TIER_EXHAUSTED` enum value still exist for defensive use by
       `job_trigger` + middleware, but `classify` never produces them for
       users who have a complete own provider.
   1c. no budget + no own provider -> `QUOTA_EXCEEDED`
       (`resolve` raises `QuotaExceededError`).
2. `prefer_system_override == False` (or no quota row = implicit opt-out):
   2a. complete own config -> `USER_OK` (route user; quota NOT consulted).
   2b. own config missing/incomplete -> `NO_PROVIDER`
       (`resolve` raises `NoProviderConfiguredError`; opt-out is honoured, no
       silent free-tier fallback).

`is_runnable(verdict)` is True only for `{SYSTEM_OK, USER_OK, SYSTEM_DISABLED}`.

## Service-call order matters

`classify` calls `is_enabled` → `quota.get` → `get_user_config` → `quota.check`
(the last ONLY on the opted-in branch). This order is load-bearing: the
disabled path returns before touching quota/user services (strict no-op
laziness), and the opt-out path never probes quota (the user pays with their
own key). Tests assert these `assert_not_called()` patterns.

## Why "all-or-nothing" for the user-complete check (MVP)

Partial config (e.g. agent slot set but embedding not) counts as incomplete.
A future iteration could merge partial user config with system config
slot-by-slot; swap `_is_user_config_complete` without changing the verdict
shape of `classify`.

## Why LLMConfig -> 3 dataclasses conversion lives here

`api_config.set_user_config` accepts three dataclasses (ClaudeConfig +
OpenAIConfig + EmbeddingConfig), not LLMConfig. The mapping
slot->protocol->dataclass is the same one `get_user_llm_configs` does
for AgentRuntime's owner-lookup path. We duplicate the shape here
intentionally — resolver's mapping is authoritative for the HTTP
request path, that function is authoritative for the agent-owner path
(background trigger / MCP tools). They share no runtime state.

## Gotchas

- Branch A must be the FIRST check. Calling `get_user_config` on every
  request in local mode would be a wasted DB round-trip and introduce
  behavioural drift.
- The conversion assumes the agent slot provider is an Anthropic-protocol
  provider and the helper_llm / embedding slots point at OpenAI-protocol
  providers. `_is_user_config_complete` does not assert the protocol
  matches — SLOT_REQUIRED_PROTOCOLS validation lives elsewhere. If a user
  wires a cross-protocol slot, the dataclass conversion will still run
  but downstream LLM SDKs may reject the resulting config.
- `QuotaExceededError` propagates up the middleware stack uncaught by
  resolver. auth_middleware must catch it explicitly and emit 402. If
  any other caller invokes `resolve_and_set` directly, it MUST handle
  `QuotaExceededError` or let it propagate.

## 2026-07-07 — inject_owner_helper_credentials（后台任务的凭据注入原语）

新增 `inject_owner_helper_credentials(agent_id, db)`：给**脱离任务**（narrative
updater、Step-5 hooks、memory worker）在自身 ContextVar 上放 agent OWNER 的有效 LLM
配置。这些 task 不继承 `AgentRuntime.run`（async generator）设的 per-turn 配置，此前
一路回退到全局 `_holder` = 平台 `settings.openai_api_key`（2026-07 事故根因）。

先 `clear_user_config()` 再查 owner、`resolve_and_set_provider_for_user`，避免复用该
协程处理别的租户时继承上一租户凭据。无 owner → 返回 None（保持清空，严格全局兜底）；
配额/无 provider → 抛 `ProviderResolverError`（调用方隔离并发告警，绝不落平台 key）。
`memory_consolidation_worker._inject_owner_credentials` 现委托此函数（去重）。
