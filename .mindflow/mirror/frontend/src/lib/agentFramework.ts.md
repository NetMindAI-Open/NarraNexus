---
code_file: frontend/src/lib/agentFramework.ts
last_verified: 2026-10-16
stub: false
---

## 2026-10-16 (PR#413 round 2: N2/N3/N4) — 默认表改为显式参数,无模块级状态

删除模块级可变表 `RECOMMENDED_*_MODEL_BY_SOURCE` 与 `applyOnboardDefaults`(它"先清后填"非原子,失败窗口会把有值表变空)。改为导出类型 `OnboardDefaults`,
`defaultAgentModel` / `defaultHelperModel` / `recommendedHelperModel` 多一个**显式** `defaults` 参数;两个面板把 `refreshOnboardDefaults()` 的结果放进 React state
(整体 `setState` 替换,失败时保留旧表)再传入。`{}`(未加载)时一律回落列表首个。
服务端下发口径(N2 钉死):**每个有 onboarding 默认的 source**(netmind / netmind_free / yunwu / openrouter),不是 NetMind 专属白名单,
UI 因此与后端 onboarding / 自愈对这些卡的取值一致(yunwu/openrouter 的 agent 默认在卡上时为 `claude-opus-4-8`)。测试:
`test_self_heal_onboard_default.py::test_onboard_defaults_map_covers_sources_only_and_follows_env` 断言 yunwu/openrouter 条目。
注意:下方(更早的)条目里凡提到"`applyOnboardDefaults` 填充模块级表"的描述均已作废,以本条为准。

## 2026-10-16 (复审 M2 回归修正) — 推荐提示只点名"刻意选择"

上一条把 `recommendedHelperModel` 直接等同 `defaultHelperModel` 是错的:后者无刻意选择在卡上时回落 `modelIds[0]`,于是"推荐:{{model}} — 小而快的模型"
会在精选旗舰卡上写出 `claude-opus-4-8`、在 NetMind 卡上写出 omni 模型。现拆出内部 `deliberateHelperModel`(source 表 → 协议表 → OAuth 别名,均须在卡上,否则 '')。
`defaultHelperModel` = 刻意选择 || 列表首个(自动选仍可回落首个);`recommendedHelperModel` = 刻意选择 || 协议表模型 || gpt-5.4-mini(**绝不回落列表首个**)。
因此提示与自动选"在有刻意选择时一致",无刻意选择时二者分叉是有意的:自动选必须选个东西,提示不能把首个说成推荐。下方(更早的)条目里"必然一致/不会指向下拉里没有的模型"的说法已作废
(无刻意选择时提示显示协议表模型,它可能不在下拉里)。`applyOnboardDefaults` 参数现为必填对象(无 undefined 兼容)。

## 2026-10-16 (PR#413 review I2+M2) — 推荐默认改由服务端下发

`RECOMMENDED_AGENT/HELPER_MODEL_BY_SOURCE` 不再含任何硬编码模型 id(删除 `NETMIND_DEFAULT_MODEL`),由 `applyOnboardDefaults(onboard_defaults)`
整体替换填充(数据来自 `GET /api/providers/catalog`,见 [[onboardDefaults]])。数据到达前/老服务端下表为空,所有选择回落"列表首个",确定且非空。
`recommendedHelperModel(source, protocol, modelIds)` 现直接复用 `defaultHelperModel`(仅无 provider、无 ids 时回落协议表),
因此"推荐:…"提示与选 provider 的自动选择必然一致、也不会指向下拉里没有的模型(取代上一条里"与自动选一致"的措辞,该措辞此前并不成立)。
下方(更早的)条目里关于"镜像后端 `_ONBOARD_*_MODELS`"的描述已作废。

## 2026-10-16 (预审修订) — recommendedHelperModel

新增 `recommendedHelperModel(source, protocol)`:"推荐:…"提示文案走与 `defaultHelperModel` 相同的优先级(source 表 → 协议表 → gpt-5.4-mini),
此前 NetMind 卡上提示仍写 gpt-5.4-mini 而自动选的是 V4.1-Flash。`ModelDefaultsSettings` / `AgentLlmConfigPanel` 的 `helperRecModel` 改用它。

## 2026-10-16 — defaultAgentModel + 按 source 的推荐默认

新增 `RECOMMENDED_AGENT/HELPER_MODEL_BY_SOURCE`(镜像后端 `_ONBOARD_*_MODELS` 的 netmind / netmind_free 条目:
`deepseek-ai/DeepSeek-V4.1-Flash`)与 `defaultAgentModel(source, modelIds)`。选 provider 时默认模型:来源默认在卡上就用它,
否则回退首个——NetMind 同步列表表头是 omni 模型,取 `models[0]` 是同一类错误。`defaultHelperModel` 先查 source 表再查协议表。
`ModelDefaultsSettings` / `AgentLlmConfigPanel` 的 agent provider 切换改用 `defaultAgentModel`。

## 2026-09-11 — `providerBacksFramework` no longer reads "no framework" as Claude Code

The subscription-card branch compared `(framework || 'claude_code')` with the
card's redeemer, so an unresolved framework silently became Claude Code — which
on the lightweight local build is not installed. It is now
`!!framework && framework === redeemer`: nothing redeems a subscription card
until a framework is actually chosen. API-key / bearer cards are unaffected.

## 2026-09-07 — CORRECTION: `protocol` is a 3-valued enum (`'anthropic' | 'openai' | 'any'`); the id-based NexusPower gate is gone (B6, supersedes the entry below)

The entry directly below this one shipped an `isNexusPowerFramework(framework)` id-based bypass
for the protocol gate, reasoning that the live list's `protocol` field could only ever be a
single concrete protocol and so could never express "accepts either". That premise was wrong:
the backend's `FrameworkMeta.protocol` (what actually lands in `LiveFrameworkEntry.protocol`) is
a **three-valued enum** — `'anthropic' | 'openai' | 'any'` — and NexusPower is sent as `'any'`.

`providerBacksFramework` now has **no framework-id branch at all**: `fw.protocol !== 'any' &&
fw.protocol !== prov.protocol` is the entire gate. `'any'` accepts every provider protocol for
ANY framework the live list marks that way, not just nexus_power by name — proven by a dedicated
test using a hypothetical `some_future_framework` with `protocol: 'any'`, plus the converse
(`nexus_power` pinned to a concrete protocol in the live list IS gated like anything else). The
type is now `LiveFrameworkEntry.protocol?: 'anthropic' | 'openai' | 'any'` (was `string`), and
`lib/api.ts`'s `getAgentFramework()` return type matches. `isNexusPowerFramework` itself is
untouched (still exported, still has zero other callers in this codebase per grep — it was never
consulted by anything else, so removing its use here doesn't orphan a caller elsewhere).

## 2026-09-07 (superseded by the correction above) — `providerBacksFramework` final cut: `frameworks` is now REQUIRED and fails closed; `CLI_FRAMEWORK_BY_OAUTH_SOURCE` and `frameworkAcceptsProtocol` deleted (B6)

Supersedes the same-day entry below (kept struck through in spirit, replaced in full — that
version was an intermediate cut that still had a hardcoded fallback; the coordinator asked for
the fallback to be removed entirely in a follow-up instruction the same day).

`providerBacksFramework(prov, framework, frameworks)` — the third parameter is no longer
optional-with-fallback: it is `LiveFrameworkEntry[] | undefined`, and **no list means the
function returns `false`**, full stop. There is no hardcoded table left to consult. Both gates
(protocol, subscription redemption) read exclusively off the live `frameworks[]` array from
`GET/POST /api/providers/agent-framework` (`protocol` and `oauth_source` per entry). Deleted
entirely: `CLI_FRAMEWORK_BY_OAUTH_SOURCE` (its only consumer was this function) and
`frameworkAcceptsProtocol` (its only consumer was this function; its signature — an
`AgentFramework` object — no longer matched what's available here). `availableFrameworks`
gained the same third `frameworks` parameter and forwards it unchanged.

New exported type `LiveFrameworkEntry` (`{ name, protocol?, oauth_source? }`) — the narrow shape
both functions actually read, so a caller doesn't need to import `lib/api.ts`'s wider
`getAgentFramework()` response type just to type the variable it's forwarding.

**One deliberate carve-out, not a hardcoded fallback**: NexusPower accepts EITHER protocol (see
`AGENT_FRAMEWORKS`'s `nexus_power` entry — `protocols: ['anthropic', 'openai']`, still present
and still read by `frameworkBrand.ts` / the two pickers' "frameworksHidden" length check /
`availableFrameworks`'s own-providers-empty early return — it was never a candidate for removal,
only `CLI_FRAMEWORK_BY_OAUTH_SOURCE` and the protocol-matching half of the old
`providerBacksFramework` were). The live list's schema carries one `protocol` string per
framework and cannot express "accepts both"; narrowing NexusPower to whichever protocol the
backend happens to report as primary would silently break an openai-only wallet's ability to
pick it (a real regression, not a hypothetical — this is exactly the scenario the 2026-07-31
entry below fixed). So the protocol gate is skipped for NexusPower specifically, by id
(`isNexusPowerFramework`), not by falling back to `AGENT_FRAMEWORKS`. Flagged for the
coordinator: this assumes the backend's live `protocol` field for `nexus_power`, if present,
does NOT need to gate anything — if a future requirement needs per-framework MULTI-protocol
data from the live list, the schema itself needs a `protocols` array, not a workaround here.

Every call site that has the list loaded must now pass it (`ModelDefaultsSettings.tsx` /
`AgentLlmConfigPanel.tsx` both hold it in a new `liveFrameworks` state, seeded from
`getAgentFramework()`'s response alongside the existing `frameworkAvailability` map, and kept
`undefined` — not `[]` — until a response actually lands, so "not loaded yet" and "loaded but
nothing matches" stay distinguishable).

## 2026-09-07 (superseded same day, see above) — `providerBacksFramework` reads `oauth_source` off the live framework list when given one (B6)

`providerBacksFramework(prov, framework, frameworks?)` gained an optional third parameter: the
LIVE `frameworks[]` list from `GET/POST /api/providers/agent-framework`, whose entries now
optionally carry their own `oauth_source` (which CLI subscription credential, if any, that
framework redeems). When passed, the redeeming framework for `prov.source` is found by scanning
this live list (`frameworks.find(f => f.oauth_source === prov.source)`) instead of the hardcoded
`CLI_FRAMEWORK_BY_OAUTH_SOURCE` mirror; the mirror remains the fallback whenever the list is
absent (older backend, or a caller with none in hand) or has no entry for that oauth_source — the
3 existing call sites that pass no third argument are unaffected (exact previous behavior).

Scoping note: `AGENT_FRAMEWORKS` and `CLI_FRAMEWORK_BY_OAUTH_SOURCE` were NOT removed — the
original ask was to "drop the hardcoded framework tables" entirely, but that would require
threading the live list through `frameworkAcceptsProtocol`'s multi-protocol check too (NexusPower's
`protocols: ['anthropic', 'openai']`), and I could not confirm the live payload carries anything
beyond a single primary `protocol` per entry without reading the backend serializer directly
(out of this file's — and this session's — edit scope). A full removal touching
`frameworkAcceptsProtocol`, `availableFrameworks`, `frameworkBrand.ts`, and the 3+ call sites that
still read `AGENT_FRAMEWORKS` directly is a larger, separate refactor.

## 2026-08-28 — 插件安装可用性合并（`frameworkAvailabilityMap` / `withFrameworkAvailability`）

轻量化插件化后 Claude Code / Codex CLI 从桌面镜像里移出，改成用户按需装的本地
插件（见 [[PluginsSettings]] / `backend/integrations/plugins`）。`GET/POST
/api/providers/agent-framework` 的响应新增可选 `frameworks: [{name, available}]`
——`frameworkAvailabilityMap()` 把它拆成 name→available 的查表，
`withFrameworkAvailability()` 把这张表贴到 `AgentFramework[]` 上（新增可选字段
`available`），`isFrameworkAvailable()` 判定。

三个函数刻意和 `availableFrameworks()`（钱包能不能驱动）分层：那个函数的语义是
**隐藏**死路选项；这里的语义是**禁用但仍列出**——插件没装的框架必须让用户看得
见、点得到"去装"，隐藏就等于把安装入口也一起藏没了。两个选择器
（[[ModelDefaultsSettings]] / [[AgentLlmConfigPanel]]）都是先调
`availableFrameworks()` 再套这层。

`frameworkAvailabilityMap` 对 `frameworks` 缺失的键（旧后端没有这个字段，或者
某个框架不是插件如 nexus_power）一律按"可用"处理——fail-closed 在这里是错的
方向：字段还没上线就把所有框架锁死，用户会以为整个功能坏了。
## 2026-08-28 — ProviderSummary 改为 providersApi.ProviderRow 的别名(PR #376 bot 轮)

本模块曾持有行形状的**第四份**结构副本,三个消费方
(AgentLlmConfigPanel / ComposerModelBadge / ModelDefaultsSettings)拿着
已经类型正确的 `api.getProviders()` 结果再 `as` 回这份窄副本——断言不是
转换,它关掉了三处的类型检查(后端上次加 netmind_account_email 这类字段
时就会静默漏掉)。现在 `export type ProviderSummary = ProviderRow`,三处
cast 删除。**import type 引 ProviderRow 是刻意的**:providersApi 运行时
import @/lib/api,值导入会造成 agentFramework → providersApi → api 运行时
环(api.ts 只做 type-only 反向引用才无环)。

## 2026-07-31 — `providerBacksFramework()` + `availableFrameworks()`

`providerBacksFramework(prov, framework)` 是后端
[[provider_schema]] `framework_can_drive_provider` 的孪生：protocol 门 +
订阅凭据门。两个 agent-slot provider 下拉从 `frameworkAcceptsProtocol` 换成
它——只查协议时，claude_oauth（anthropic）在 NexusPower 下照样可选，存得进
库，跑起来才炸。

`availableFrameworks(providers, current)` 决定框架下拉列什么：钱包里没有一张
卡能驱动的框架是死路（选中后 provider 下拉直接空），所以隐藏。两个刻意的
逃生口：**providers 为空不过滤**（还没加卡的用户不该面对空下拉）、
**current 永远保留**（丢掉选中值会让 `<select>` 悄悄改指向另一个框架）。

**它不查云端 staff-only 规则**（`frameworkAllowedInCloud`）：那两处选择器是
故意把云端禁用的框架继续列出来、选中时弹解释，比悄悄变短的列表友好。两条
规则问的是不同的问题，别合并。

## 2026-07-29 — `frameworkAllowedInCloud()`：后端云策略的前端孪生

新增 `CLOUD_ALLOWED_FRAMEWORKS` + `frameworkAllowedInCloud()`，对应后端
`cloud_policy.CLOUD_ALLOWED_FRAMEWORKS`。两个框架选择器（
[[ModelDefaultsSettings]] 与 [[AgentLlmConfigPanel]]）此前各自内联了
`!== 'claude_code'`，NexusPower 变成云端合法之后**两处都还在拒**——所以这里
导出的是**谓词**而不是常量，与 `isSlotBindableSource` 同样的教训。

## 2026-07-29 — 框架可接受协议从"一个"变成"一组"

新增可选 `protocols` 与 `frameworkAcceptsProtocol()`:CLI 型框架天生只会一种
协议(claude_code→anthropic、codex_cli→openai,因为底下的 CLI 只会一种),而
NexusPower 直接驱动 provider API,两种都行,**不能**被过滤成一种。UI 上叫
`NexusPower-beta`。另有 `isNexusPowerFramework()` 谓词,把散落的
`=== 'nexus_power'` 收成一处(与既有 `isCodexFramework` 同型)。

## 2026-07-17 — 新增 cloudNetmindOnly 策略谓词 + DESKTOP_RELEASES_URL

`cloudNetmindOnly(role)` = `isForcedCloud() && role !== 'staff'`——后端两个
route 门禁（providers.py / agents/llm_config.py 的云端 netmind-only 槽位策略）
的前端孪生。两个槽位编辑器（[[ModelDefaultsSettings]] +
[[AgentLlmConfigPanel]]）都经它过滤 provider 下拉（`source !== 'netmind'`
隐藏），保证 UI 不给出会被 403 的选项。role 由调用方从 configStore 读出传入
（本 lib 保持纯函数，不 import store）。注意与铁律 #15 的区别：这不是"平台
判断 provider 合不合适"，而是云端商业策略（自有 key = 本地版功能）。
`DESKTOP_RELEASES_URL`（NetMindAI-Open releases 页）随之导出，供"下载本地版"
提示链接复用。本文件首次 import runtimeConfig（isForcedCloud）。

## 2026-07-10 — 删 CODEX_ALLOWED_PROVIDER_SOURCES + curated 收窄到 codex_oauth

`CODEX_ALLOWED_PROVIDER_SOURCES` 常量**已删除**。它以前是 codex_cli agent slot 的
source 白名单(`{codex_oauth, user}`),配合后端 `validate_slot_binding` 把 NetMind /
Yunwu / OpenRouter 挡在外面。按铁律 #15(平台不替用户判断 provider 是否合适)整条
移除,恢复 pre-#81 行为——codex agent slot 现在只查 protocol。调用它的两处过滤
([[AgentLlmConfigPanel]] / ModelDefaultsSettings)同步删掉 source 分支,只留
`p.protocol !== fw.protocol`。

`getModelsForSlot` 的 codex 分支**收窄到 `prov.source === 'codex_oauth'`**:只有
OpenAI 自己的 codex 后端才强制 `CODEX_CURATED_MODELS`(它按账号 tier 网关);其他
openai provider(聚合商/自填 base_url)返回自己的 `prov.models`。与后端
`get_user_service.get_user_config`(同样 codex_oauth-only 覆盖)对齐。修掉"选了
netmind 却只看得到 gpt-5.x 三个模型"的 bug。详见后端 mirror
[[user_service]] 2026-07-10 条目。

## 2026-07-09 — defaultHelperModel(选 helper provider 时默认便宜模型)

新增 `defaultHelperModel(source, protocol, modelIds)`:helper slot 选定 provider 后,
默认挑**推荐的便宜模型**而非 `models[0]`(旗舰)。优先 `RECOMMENDED_HELPER_MODEL_BY_PROTOCOL`
(openai→gpt-5.4-mini / anthropic→claude-haiku-4-5);OAuth provider 列的是 CLI 别名、
具体推荐 id 可能不在列表里,故映射到后端 auto-bind 同款别名(claude_oauth→`haiku`、
codex_oauth→`gpt-5.4-mini`),都不在时才回退首个。`ModelDefaultsSettings` 与
`AgentLlmConfigPanel` 共用,修掉"选 codex 后 helper 默认成 gpt-5.5 旗舰 / 选 claude 后默认
opus"的问题。

## 2026-07-09 — shared framework/model helpers for the provider UI

Single source of truth for the LLM provider/slot UI, shared by the user-level
Settings editor ([[ProviderSettings]]) and the per-agent chat surfaces
([[ComposerModelBadge]], [[AgentLlmConfigPanel]]) so a per-agent override offers
exactly the same choices as the global-default editor.

Holds: ``AGENT_FRAMEWORKS`` + ``isCodexFramework``; ``CODEX_CURATED_MODELS``
(codex_oauth-only — mirror of backend ``providers.user_service``);
``RECOMMENDED_HELPER_MODEL_BY_PROTOCOL`` (mirror of backend
``_ONBOARD_HELPER_MODELS``); ``MODEL_SUGGESTION_GROUPS``; reasoning option
lists; and ``getModelsForSlot(prov, slot, framework, knownModels)`` (agent+codex
**on codex_oauth** → curated set, every other provider → its own models). These
were previously local to ProviderSettings; extracting them avoided duplicating
the codex rules across the new per-agent components. (The old
``CODEX_ALLOWED_PROVIDER_SOURCES`` source-allowlist was removed 2026-07-10 — see
the dated entry above.)
