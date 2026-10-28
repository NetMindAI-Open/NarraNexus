---
code_file: frontend/src/lib/providersApi.ts
last_verified: 2026-09-09
stub: false
---

## 2026-09-09 — `CliStatusPayload.expired?`

两条 status 路由（`/claude-status` 自 B-25 起、`/codex-status` 自 2026-06-11 起）都在发
`expired`，类型里一直没有——三个月的死字段。可选，因为路由早于它、测试 mock 不带它。
唯一消费者 [[../components/settings/SubscriptionConnect.tsx]] 的 `CliStatusLine` 三态。

## 2026-09-03 — `ProviderRow.auto_provisioned`

后端标记「login 替用户开的卡」；[[onboardingGate.ts]] 唯一消费者。

# providersApi.ts — providers 族的共享类型/映射 + ProviderSettings 的 legacy 裸 fetch

## 现在的定位(review 第 3 轮重构后)

**新代码的传输层在 ApiClient**(api.addProvider / getClaudeStatus /
getCodexStatus / getProviders)——只有它带 session-death 守卫(401 +
confirmSessionDeath,2026-08-02 事故正是本资源)和 FastAPI detail 提取。
本模块保留:

- `ProviderRow` / `CliStatusPayload` — 共享类型(api.ts type-only 反向
  引用,无运行时环;`allowed` 仅 cloud 非 staff 为 false,判据必须
  `=== false`)。第 4 轮:ProviderRow 从"最窄可选形状"改为**唯一的行
  类型**(收编 ProviderSettings 的 ProviderSummary 超集字段,按后端实际
  契约标注可选性)——窄形状逼出过 `as unknown as` 双重 cast,把"有类型"
  演成了"没类型"。三份行形状 → 一份;别再本地重declare。PR bot 轮补漏:agentFramework.ts
藏着第四份(三个 chat/settings 消费方 cast 进它),现同为别名、cast 全删
——"唯一行类型"至此才真正成立。providerErrorMessage 对 **422**(FastAPI
校验错误,detail 是 JSON 数组)回落 generic failed 文案,不把原始数组当
用户文案渲染。
- `addProviderCard(body, t)` — POST + 错误文案的**唯一**包装(两个调用方
  曾各抄一份 try/catch);**无刷新副作用**(各调用方自刷是明确决策)。
- `providerErrorMessage(err, t)` — api.addProvider 失败(抛 ApiError)
  → 用户文案的单份映射:有 detail 原样透出、ApiError 无 detail(如网关
  502 HTML)→ failed、非 ApiError(fetch 拒绝)→ networkError。
- `authFetch` / `providerApiUrl` — **LEGACY,仅供 ProviderSettings 既有
  裸端点**(delete/test/models/sync;迁移是后续项)。新调用方禁止使用
  ——它没有 session 守卫,这正是 review 打回第一版 postProvider 的原因。

## 历史教训(保留)

- authFetch 委托 [[authHeaders]] 单一解析点;第一稿手抄解析被打回
  (2026-05-18 跨用户 bug 族)。header 用 `headers.set` 逐个写,
  `new Headers(getAuthHeaders())` 会丢 Content-Type。
- 第二稿的 `postProvider`(裸 fetch、不查 response.ok)被第 3 轮打回:
  session-dead 401 只会变成一行红字,502 HTML 被说成"网络错误"。
- 仓库仍有三处遗留手工解析(api.ts / arenaLanding.ts / artifactsApi.ts),
  别加第四处。
